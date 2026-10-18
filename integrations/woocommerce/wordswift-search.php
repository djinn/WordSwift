<?php
/**
 * Plugin Name:       WordSwift Search
 * Plugin URI:        https://github.com/djinn/WordSwift
 * Description:       Replaces WooCommerce's default search with WordSwift's BM25 + typo-tolerant + semantic search.
 * Version:           1.0.0
 * Requires at least: 5.8
 * Requires PHP:      7.4
 * Author:            Supreet Sethi
 * License:           MIT
 * Text Domain:       wordswift-search
 * WC requires at least: 6.0
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit; // No direct access.
}

define( 'WORDSWIFT_VERSION', '1.0.0' );
define( 'WORDSWIFT_FILE', __FILE__ );
define( 'WORDSWIFT_DIR', plugin_dir_path( __FILE__ ) );

require_once WORDSWIFT_DIR . 'includes/class-wordswift-client.php';
require_once WORDSWIFT_DIR . 'includes/class-wordswift-sync.php';
require_once WORDSWIFT_DIR . 'includes/class-wordswift-admin.php';

/**
 * Main plugin class.
 */
final class WordSwift_Search {

	/** @var WordSwift_Search|null */
	private static $instance = null;

	/** @var WordSwift_Client */
	public $client;

	/** @var WordSwift_Sync */
	public $sync;

	public static function instance() {
		if ( null === self::$instance ) {
			self::$instance = new self();
		}
		return self::$instance;
	}

	private function __construct() {
		$this->client = new WordSwift_Client(
			self::get_option( 'url', 'http://localhost:7700' ),
			self::get_option( 'index', 'woocommerce_products' ),
			(float) self::get_option( 'timeout', 10 )
		);
		$this->sync = new WordSwift_Sync( $this->client );

		add_action( 'init', array( $this, 'load_textdomain' ) );
		add_action( 'admin_menu', array( $this, 'admin_menu' ) );
		add_action( 'admin_init', array( $this, 'register_settings' ) );

		// Search interception.
		add_action( 'pre_get_posts', array( $this, 'intercept_search' ), 20 );

		// Incremental indexing on product changes.
		add_action( 'save_post_product', array( $this, 'on_product_save' ), 10, 2 );
		add_action( 'before_delete_post', array( $this, 'on_product_delete' ), 10, 1 );
		add_action( 'woocommerce_update_product', array( $this, 'on_product_save' ), 10, 1 );

		// AJAX endpoints for the admin screen.
		add_action( 'wp_ajax_wordswift_resync', array( $this, 'ajax_resync' ) );
		add_action( 'wp_ajax_wordswift_test', array( $this, 'ajax_test' ) );
	}

	public function load_textdomain() {
		load_plugin_textdomain(
			'wordswift-search',
			false,
			dirname( plugin_basename( WORDSWIFT_FILE ) ) . '/languages/'
		);
	}

	// ------------------------------------------------------------------
	// Options
	// ------------------------------------------------------------------
	public static function get_option( $key, $default = '' ) {
		$options = get_option( 'wordswift_settings', array() );
		return isset( $options[ $key ] ) ? $options[ $key ] : $default;
	}

	public function admin_menu() {
		add_options_page(
			__( 'WordSwift Search', 'wordswift-search' ),
			__( 'WordSwift', 'wordswift-search' ),
			'manage_woocommerce',
			'wordswift',
			array( 'WordSwift_Admin', 'render_page' )
		);
	}

	public function register_settings() {
		register_setting(
			'wordswift_settings_group',
			'wordswift_settings',
			array( 'sanitize_callback' => array( __CLASS__, 'sanitize_settings' ) )
		);
	}

	public static function sanitize_settings( $input ) {
		$clean = array();

		$clean['url'] = isset( $input['url'] )
			? untrailingslashit( esc_url_raw( $input['url'] ) )
			: 'http://localhost:7700';

		$clean['index'] = isset( $input['index'] )
			? sanitize_key( $input['index'] )
			: 'woocommerce_products';

		$clean['timeout'] = isset( $input['timeout'] )
			? max( 1, min( 60, (float) $input['timeout'] ) )
			: 10;

		$clean['hybrid']         = ! empty( $input['hybrid'] ) ? 1 : 0;
		$clean['typo_tolerance'] = ! empty( $input['typo_tolerance'] ) ? 1 : 0;
		$clean['limit']          = isset( $input['limit'] )
			? max( 1, min( 100, (int) $input['limit'] ) )
			: 20;
		$clean['enabled']        = ! empty( $input['enabled'] ) ? 1 : 0;

		return $clean;
	}

	// ------------------------------------------------------------------
	// Search interception
	// ------------------------------------------------------------------
	/**
	 * Replace WooCommerce's LIKE-based search with WordSwift results.
	 *
	 * Only touches the main frontend search query. Leaves admin, secondary
	 * queries and non-search queries alone.
	 */
	public function intercept_search( $query ) {
		if ( is_admin() || ! $query->is_main_query() || ! $query->is_search() ) {
			return;
		}
		if ( ! self::get_option( 'enabled', 0 ) ) {
			return;
		}

		$term = trim( (string) $query->get( 's' ) );
		if ( '' === $term ) {
			return;
		}

		$hits = $this->client->search(
			$term,
			(int) self::get_option( 'limit', 20 ),
			(bool) self::get_option( 'hybrid', 0 ),
			(bool) self::get_option( 'typo_tolerance', 1 )
		);

		// On failure, fall back to WooCommerce's native search rather than
		// showing customers an empty page.
		if ( is_wp_error( $hits ) ) {
			$this->log( 'search failed: ' . $hits->get_error_message() );
			return;
		}

		// Drop products tombstoned since the last resync. This matters for items
		// set to "hidden" catalog visibility: they are still *published* posts,
		// so post__in below would otherwise return them to shoppers.
		$hits = $this->sync->filter_deleted( $hits );

		$ids = wp_list_pluck( $hits, 'id' );
		$ids = array_values( array_filter( array_map( 'intval', $ids ) ) );

		if ( empty( $ids ) ) {
			return; // Nothing found — let WooCommerce handle the no-results UI.
		}

		// Preserve WordSwift's ranking. Without 'post__in' ordering WordPress
		// re-sorts by its own relevance/date and discards the BM25 scores.
		$query->set( 'post__in', $ids );
		$query->set( 'orderby', 'post__in' );
		$query->set( 's', '' );
		$query->set( 'post_type', array( 'product' ) );

		// Store the ranking so templates can show relevance if desired.
		$query->set( 'wordswift_ids', $ids );
	}

	// ------------------------------------------------------------------
	// Incremental indexing
	// ------------------------------------------------------------------
	public function on_product_save( $post_id, $post = null ) {
		if ( ! self::get_option( 'enabled', 0 ) ) {
			return;
		}
		if ( wp_is_post_revision( $post_id ) || wp_is_post_autosave( $post_id ) ) {
			return;
		}

		// Only index published/private products; trash and drafts are removed.
		$status = get_post_status( $post_id );
		if ( ! in_array( $status, array( 'publish', 'private' ), true ) ) {
			$this->sync->remove( $post_id );
			return;
		}

		$this->sync->index_product( $post_id );
	}

	public function on_product_delete( $post_id ) {
		if ( 'product' !== get_post_type( $post_id ) ) {
			return;
		}
		$this->sync->remove( $post_id );
	}

	// ------------------------------------------------------------------
	// AJAX (admin screen)
	// ------------------------------------------------------------------
	public function ajax_resync() {
		check_ajax_referer( 'wordswift_admin', 'nonce' );

		if ( ! current_user_can( 'manage_woocommerce' ) ) {
			wp_send_json_error( array( 'message' => __( 'Permission denied.', 'wordswift-search' ) ), 403 );
		}

		$result = $this->sync->full_resync();

		if ( is_wp_error( $result ) ) {
			wp_send_json_error( array( 'message' => $result->get_error_message() ) );
		}

		wp_send_json_success( $result );
	}

	public function ajax_test() {
		check_ajax_referer( 'wordswift_admin', 'nonce' );

		if ( ! current_user_can( 'manage_woocommerce' ) ) {
			wp_send_json_error( array( 'message' => __( 'Permission denied.', 'wordswift-search' ) ), 403 );
		}

		$query = isset( $_GET['q'] ) ? sanitize_text_field( wp_unslash( $_GET['q'] ) ) : '';
		if ( '' === $query ) {
			wp_send_json_error( array( 'message' => __( 'Empty query.', 'wordswift-search' ) ) );
		}

		$health = $this->client->health();
		$hits   = $this->client->search( $query, 10, true, true );

		wp_send_json_success(
			array(
				'health' => is_wp_error( $health ) ? $health->get_error_message() : $health,
				'hits'   => is_wp_error( $hits ) ? array() : $hits,
				'error'  => is_wp_error( $hits ) ? $hits->get_error_message() : null,
			)
		);
	}

	// ------------------------------------------------------------------
	// Utilities
	// ------------------------------------------------------------------
	public static function log( $message ) {
		if ( defined( 'WP_DEBUG' ) && WP_DEBUG ) {
			error_log( '[WordSwift] ' . $message ); // phpcs:ignore
		}
	}
}

/**
 * @return WordSwift_Search
 */
function wordswift() {
	return WordSwift_Search::instance();
}

// Boot after WooCommerce is available.
add_action(
	'plugins_loaded',
	static function () {
		if ( ! class_exists( 'WooCommerce' ) ) {
			WordSwift_Search::log( 'WooCommerce not active — plugin idle.' );
			return;
		}
		WordSwift_Search::instance();
	},
	20
);

// Declare HPOS (custom order tables) compatibility — we only touch products.
add_action(
	'before_woocommerce_init',
	function () {
		if ( class_exists( \Automattic\WooCommerce\Utilities\FeaturesUtil::class ) ) {
			\Automattic\WooCommerce\Utilities\FeaturesUtil::declare_compatibility(
				'custom_order_tables',
				WORDSWIFT_FILE,
				true
			);
		}
	}
);
