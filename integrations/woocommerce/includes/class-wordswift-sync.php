<?php
/**
 * Product sync: WooCommerce product → WordSwift document.
 *
 * @package WordSwift_Search
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

class WordSwift_Sync {

	/** @var WordSwift_Client */
	private $client;

	/**
	 * Product ids deleted from WooCommerce.
	 *
	 * WordSwift's API has no per-document delete, so we persist tombstones and
	 * filter them out of search results. See docs/ECOMMERCE.md.
	 *
	 * @var array
	 */
	private $tombstones;

	const TOMBSTONE_OPTION = 'wordswift_tombstones';

	public function __construct( WordSwift_Client $client ) {
		$this->client     = $client;
		$this->tombstones = get_option( self::TOMBSTONE_OPTION, array() );
		if ( ! is_array( $this->tombstones ) ) {
			$this->tombstones = array();
		}
	}

	// ------------------------------------------------------------------
	// Transform
	// ------------------------------------------------------------------
	/**
	 * Build the WordSwift document for a product.
	 *
	 * WordSwift only indexes string fields, so every value is cast to string.
	 * The id stays an integer: the registry coerces it to int and falls back to
	 * an auto-increment counter for non-numeric strings.
	 *
	 * @param int|WC_Product $product
	 * @return array|null Null when the product should not be searchable.
	 */
	public function build_document( $product ) {
		if ( is_numeric( $product ) ) {
			$product = wc_get_product( (int) $product );
		}

		if ( ! $product instanceof WC_Product ) {
			return null;
		}

		$post_id = $product->get_id();
		$status  = get_post_status( $post_id );

		// Only published/private products are searchable.
		if ( ! in_array( $status, array( 'publish', 'private' ), true ) ) {
			return null;
		}

		// Respect WooCommerce catalog visibility. 'hidden' and 'catalog' exclude
		// the product from search results; indexing them would leak products the
		// merchant deliberately hid. (WooCommerce: Visibility → "Hidden" or
		// "Catalog only".)
		$visibility = (string) $product->get_catalog_visibility();
		if ( ! in_array( $visibility, array( 'visible', 'search' ), true ) ) {
			return null;
		}

		$categories = wp_get_post_terms( $post_id, 'product_cat', array( 'fields' => 'names' ) );
		$tags       = wp_get_post_terms( $post_id, 'product_tag', array( 'fields' => 'names' ) );

		$categories = is_wp_error( $categories ) ? array() : $categories;
		$tags       = is_wp_error( $tags ) ? array() : $tags;

		// Strip HTML/shortcodes so BM25 sees clean prose.
		$description = wp_strip_all_tags(
			str_replace( ']', '] ', (string) $product->get_description() )
		);
		$short_description = wp_strip_all_tags( (string) $product->get_short_description() );

		$image_id  = $product->get_image_id();
		$image_url = $image_id ? wp_get_attachment_url( $image_id ) : '';

		$attributes = array();
		foreach ( $product->get_attributes() as $attribute ) {
			if ( $attribute instanceof WC_Product_Attribute ) {
				$options = $attribute->get_options();
				if ( $attribute->is_taxonomy() ) {
					$terms   = get_terms(
						array(
							'taxonomy'   => $attribute->get_name(),
							'hide_empty' => false,
							'fields'     => 'names',
						)
					);
					$options = is_wp_error( $terms ) ? array() : $terms;
				}
				$attributes[] = $attribute->get_name() . ': ' . implode( ', ', (array) $options );
			}
		}

		$doc = array(
			'id'                => $post_id, // integer on purpose
			'title'             => (string) $product->get_name(),
			'sku'               => (string) $product->get_sku(),
			'description'       => $description,
			'short_description' => $short_description,
			'price'             => (string) $product->get_price(),
			'regular_price'     => (string) $product->get_regular_price(),
			'categories'        => implode( ', ', $categories ),
			'tags'              => implode( ', ', $tags ),
			'attributes'        => implode( ' | ', $attributes ),
			'permalink'         => (string) get_permalink( $post_id ),
			'image'             => (string) $image_url,
			'in_stock'          => $product->is_in_stock() ? 'yes' : 'no',
			'type'              => (string) $product->get_type(),
		);

		// Variations carry their own SKUs and prices — strong search signals.
		if ( $product->is_type( 'variable' ) ) {
			$variation_ids = $product->get_children();
			$skus          = array();
			$prices        = array();

			foreach ( $variation_ids as $variation_id ) {
				$variation = wc_get_product( $variation_id );
				if ( ! $variation instanceof WC_Product_Variation ) {
					continue;
				}
				if ( $variation->get_sku() ) {
					$skus[] = $variation->get_sku();
				}
				if ( $variation->get_price() ) {
					$prices[] = (float) $variation->get_price();
				}
			}

			if ( $skus ) {
				$doc['variation_skus'] = implode( ', ', $skus );
			}
			if ( $prices ) {
				$doc['price_from'] = (string) min( $prices );
				$doc['price_to']   = (string) max( $prices );
			}
		}

		// Drop empties so the index stays lean.
		return array_filter(
			$doc,
			static function ( $value ) {
				return null !== $value && '' !== $value;
			}
		);
	}

	// ------------------------------------------------------------------
	// Incremental sync
	// ------------------------------------------------------------------
	/**
	 * Index a single product. Removes it if it is no longer searchable.
	 *
	 * @return bool|WP_Error
	 */
	public function index_product( $post_id ) {
		$post_id = (int) $post_id;

		if ( 'product' !== get_post_type( $post_id ) ) {
			return false;
		}

		$doc = $this->build_document( $post_id );

		if ( null === $doc ) {
			return $this->remove( $post_id );
		}

		$ready = $this->client->ensure_index();
		if ( is_wp_error( $ready ) ) {
			return $ready;
		}

		$result = $this->client->add_documents( array( $doc ) );
		if ( is_wp_error( $result ) ) {
			WordSwift_Search::log( "index_product({$post_id}) failed: " . $result->get_error_message() );
			return $result;
		}

		$this->clear_tombstone( $post_id );
		return true;
	}

	/**
	 * Tombstone a deleted product.
	 *
	 * @return bool|WP_Error
	 */
	public function remove( $post_id ) {
		$post_id = (int) $post_id;
		$this->record_tombstone( $post_id );
		return true;
	}

	/**
	 * Filter tombstoned ids out of search results.
	 *
	 * @param array $hits
	 * @return array
	 */
	public function filter_deleted( array $hits ) {
		if ( empty( $this->tombstones ) ) {
			return $hits;
		}

		$dead = array_map( 'intval', $this->tombstones );

		return array_values(
			array_filter(
				$hits,
				static function ( $hit ) use ( $dead ) {
					return ! isset( $hit['id'] ) || ! in_array( (int) $hit['id'], $dead, true );
				}
			)
		);
	}

	// ------------------------------------------------------------------
	// Full resync
	// ------------------------------------------------------------------
	/**
	 * Re-index every product. Also clears stale tombstones.
	 *
	 * @return array|WP_Error Summary stats.
	 */
	public function full_resync( $batch_size = 50 ) {
		$ready = $this->client->ensure_index();
		if ( is_wp_error( $ready ) ) {
			return $ready;
		}

		$indexed = 0;
		$skipped = 0;
		$page    = 1;
		$limit   = max( 1, (int) $batch_size );

		do {
			$query = new WP_Query(
				array(
					'post_type'      => 'product',
					'post_status'    => array( 'publish', 'private' ),
					'posts_per_page' => $limit,
					'paged'          => $page,
					'orderby'        => 'ID',
					'order'          => 'ASC',
					'no_found_rows'  => false,
					'fields'         => 'ids',
				)
			);

			$ids = $query->posts;

			if ( empty( $ids ) ) {
				break;
			}

			$documents = array();
			foreach ( $ids as $post_id ) {
				$doc = $this->build_document( $post_id );
				if ( null === $doc ) {
					++$skipped;
					// Not searchable (unpublished, or hidden from catalog/search).
					// Tombstone so it is filtered out until it becomes visible
					// again, at which point index_product clears the tombstone.
					$this->remove( (int) $post_id );
					continue;
				}
				$documents[] = $doc;
			}

			if ( $documents ) {
				$result = $this->client->add_documents( $documents );
				if ( is_wp_error( $result ) ) {
					return new WP_Error(
						'wordswift_resync_failed',
						sprintf(
							/* translators: 1: page number, 2: error message */
							__( 'Batch on page %1$d failed: %2$s', 'wordswift-search' ),
							$page,
							$result->get_error_message()
						)
					);
				}
				$indexed += (int) $result;

				// These products are live again — drop their tombstones.
				foreach ( $documents as $doc ) {
					$this->clear_tombstone( (int) $doc['id'] );
				}
			}

			++$page;
		} while ( $page <= (int) $query->max_num_pages );

		$stats = $this->client->stats();

		return array(
			'indexed' => $indexed,
			'skipped' => $skipped,
			'stats'   => is_wp_error( $stats ) ? null : $stats,
		);
	}

	// ------------------------------------------------------------------
	// Tombstone storage
	// ------------------------------------------------------------------
	private function record_tombstone( $post_id ) {
		$post_id = (int) $post_id;
		if ( in_array( $post_id, array_map( 'intval', $this->tombstones ), true ) ) {
			return;
		}

		$this->tombstones[] = $post_id;

		// Cap the list; beyond this a full resync is the right fix.
		if ( count( $this->tombstones ) > 5000 ) {
			$this->tombstones = array_slice( $this->tombstones, -5000 );
		}

		update_option( self::TOMBSTONE_OPTION, $this->tombstones, false );
	}

	private function clear_tombstone( $post_id ) {
		$post_id = (int) $post_id;
		$current = array_map( 'intval', $this->tombstones );

		if ( ! in_array( $post_id, $current, true ) ) {
			return;
		}

		$this->tombstones = array_values(
			array_filter(
				$this->tombstones,
				static function ( $id ) use ( $post_id ) {
					return (int) $id !== $post_id;
				}
			)
		);

		update_option( self::TOMBSTONE_OPTION, $this->tombstones, false );
	}

	/**
	 * Public accessor for the admin screen.
	 *
	 * @return int
	 */
	public function tombstone_count() {
		return count( $this->tombstones );
	}
}
