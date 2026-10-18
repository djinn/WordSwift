<?php
/**
 * WordSwift HTTP client for WordPress.
 *
 * Uses the WordPress HTTP API (wp_remote_*) so it respects proxies, SSL
 * settings and site-wide transport configuration.
 *
 * @package WordSwift_Search
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

class WordSwift_Client {

	/** @var string */
	private $base_url;

	/** @var string */
	private $index;

	/** @var float */
	private $timeout;

	public function __construct( $base_url, $index, $timeout = 10.0 ) {
		$this->base_url = untrailingslashit( $base_url );
		$this->index    = $index;
		$this->timeout  = (float) $timeout;
	}

	// ------------------------------------------------------------------
	// Transport
	// ------------------------------------------------------------------
	private function request( $method, $path, $body = null, $args = array() ) {
		$url = $this->base_url . $path;

		$request_args = array_merge(
			array(
				'method'  => $method,
				'timeout' => $this->timeout,
				'headers' => array( 'Accept' => 'application/json' ),
			),
			$args
		);

		if ( null !== $body ) {
			$request_args['body']    = wp_json_encode( $body );
			$request_args['headers'] = array_merge(
				$request_args['headers'],
				array( 'Content-Type' => 'application/json' )
			);
		}

		$response = wp_remote_request( $url, $request_args );

		if ( is_wp_error( $response ) ) {
			return $response;
		}

		$code = (int) wp_remote_retrieve_response_code( $response );
		$raw  = wp_remote_retrieve_body( $response );
		$data = json_decode( $raw, true );

		if ( $code >= 400 ) {
			$detail = is_array( $data ) && isset( $data['detail'] )
				? ( is_string( $data['detail'] ) ? $data['detail'] : wp_json_encode( $data['detail'] ) )
				: substr( $raw, 0, 200 );

			return new WP_Error(
				'wordswift_http_error',
				sprintf( 'WordSwift returned HTTP %d: %s', $code, $detail ),
				array( 'status' => $code )
			);
		}

		return null === $data ? array() : $data;
	}

	// ------------------------------------------------------------------
	// Health & index management
	// ------------------------------------------------------------------
	/**
	 * @return array|WP_Error
	 */
	public function health() {
		return $this->request( 'GET', '/health' );
	}

	/**
	 * Create the index if it does not already exist.
	 *
	 * @return true|WP_Error
	 */
	public function ensure_index( $dim = 100 ) {
		$result = $this->request(
			'POST',
			'/indexes',
			array(
				'uid' => $this->index,
				'dim' => (int) $dim,
			)
		);

		if ( is_wp_error( $result ) ) {
			$data = $result->get_error_data();
			// 400 with "already exists" is success for our purposes.
			if ( isset( $data['status'] ) && 400 === (int) $data['status'] ) {
				return true;
			}
			return $result;
		}

		return true;
	}

	/**
	 * @return array|WP_Error
	 */
	public function stats() {
		return $this->request( 'GET', '/indexes/' . rawurlencode( $this->index ) . '/stats' );
	}

	// ------------------------------------------------------------------
	// Documents
	// ------------------------------------------------------------------
	/**
	 * Add or update documents in batches.
	 *
	 * @param array $documents List of associative arrays.
	 * @param int   $batch_size
	 * @return int|WP_Error Number of documents accepted.
	 */
	public function add_documents( array $documents, $batch_size = 200 ) {
		if ( empty( $documents ) ) {
			return 0;
		}

		$path    = '/indexes/' . rawurlencode( $this->index ) . '/documents';
		$added   = 0;
		$batches = array_chunk( $documents, max( 1, (int) $batch_size ) );

		foreach ( $batches as $batch ) {
			$result = $this->request( 'POST', $path, $batch );

			if ( is_wp_error( $result ) ) {
				return $result;
			}

			$added += isset( $result['documentsCount'] ) ? (int) $result['documentsCount'] : 0;
		}

		return $added;
	}

	// ------------------------------------------------------------------
	// Search
	// ------------------------------------------------------------------
	/**
	 * Run a hybrid search and return real document hits.
	 *
	 * Filters out the synthetic `_semantic` entries WordSwift emits when it
	 * falls back to vocabulary-word matches — those carry negative ids and are
	 * not products.
	 *
	 * @return array|WP_Error
	 */
	public function search( $query, $limit = 20, $hybrid = false, $typo_tolerance = true ) {
		$params = array(
			'q'              => $query,
			'limit'          => (int) $limit,
			'hybrid'         => $hybrid ? 'true' : 'false',
			'typo_tolerance' => $typo_tolerance ? 'true' : 'false',
			'prefix_search'  => 'true',
		);

		$result = $this->request(
			'GET',
			'/indexes/' . rawurlencode( $this->index ) . '/search?' . http_build_query( $params )
		);

		if ( is_wp_error( $result ) ) {
			return $result;
		}

		$hits = isset( $result['hits'] ) && is_array( $result['hits'] ) ? $result['hits'] : array();

		$filtered = array();
		foreach ( $hits as $hit ) {
			if ( ! empty( $hit['_semantic'] ) ) {
				continue;
			}
			$filtered[] = $hit;
		}

		return $filtered;
	}
}
