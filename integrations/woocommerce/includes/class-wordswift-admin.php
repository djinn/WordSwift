<?php
/**
 * Admin settings screen for WordSwift Search.
 *
 * @package WordSwift_Search
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

class WordSwift_Admin {

	/**
	 * Render the settings page under Settings → WordSwift.
	 */
	public static function render_page() {
		if ( ! current_user_can( 'manage_woocommerce' ) ) {
			wp_die( esc_html__( 'You do not have permission to access this page.', 'wordswift-search' ) );
		}

		$o = get_option( 'wordswift_settings', array() );
		$o = wp_parse_args(
			$o,
			array(
				'enabled'        => 0,
				'url'            => 'http://localhost:7700',
				'index'          => 'woocommerce_products',
				'timeout'        => 10,
				'hybrid'         => 0,
				'typo_tolerance' => 1,
				'limit'          => 20,
				'min_score'      => 0,
				'fields'         => 'title,sku,description',
			)
		);

		$nonce = wp_create_nonce( 'wordswift_admin' );
		?>
		<div class="wrap wordswift-admin">
			<h1><?php esc_html_e( 'WordSwift Search', 'wordswift-search' ); ?></h1>

			<p class="description">
				<?php
				esc_html_e(
					'Replaces WooCommerce search with BM25 + typo-tolerant + semantic search.',
					'wordswift-search'
				);
				?>
			</p>

			<form method="post" action="options.php">
				<?php settings_fields( 'wordswift_settings_group' ); ?>

				<table class="form-table" role="presentation">
					<tr>
						<th scope="row"><?php esc_html_e( 'Enable', 'wordswift-search' ); ?></th>
						<td>
							<label>
								<input type="checkbox" name="wordswift_settings[enabled]" value="1"
									<?php checked( $o['enabled'], 1 ); ?> />
								<?php esc_html_e( 'Use WordSwift for product search', 'wordswift-search' ); ?>
							</label>
							<p class="description">
								<?php
								esc_html_e(
									'When WordSwift is unreachable, search silently falls back to WooCommerce.',
									'wordswift-search'
								);
								?>
							</p>
						</td>
					</tr>

					<tr>
						<th scope="row">
							<label for="ws-url"><?php esc_html_e( 'Server URL', 'wordswift-search' ); ?></label>
						</th>
						<td>
							<input type="url" id="ws-url" class="regular-text"
								name="wordswift_settings[url]" value="<?php echo esc_attr( $o['url'] ); ?>"
								placeholder="http://localhost:7700" />
							<p class="description">
								<?php esc_html_e( 'Base URL of your WordSwift instance (no trailing slash).', 'wordswift-search' ); ?>
							</p>
						</td>
					</tr>

					<tr>
						<th scope="row">
							<label for="ws-index"><?php esc_html_e( 'Index name', 'wordswift-search' ); ?></label>
						</th>
						<td>
							<input type="text" id="ws-index" class="regular-text"
								name="wordswift_settings[index]" value="<?php echo esc_attr( $o['index'] ); ?>"
								placeholder="woocommerce_products" />
						</td>
					</tr>

					<tr>
						<th scope="row">
							<label for="ws-timeout"><?php esc_html_e( 'Timeout (s)', 'wordswift-search' ); ?></label>
						</th>
						<td>
							<input type="number" id="ws-timeout" step="0.5" min="1" max="60"
								name="wordswift_settings[timeout]" value="<?php echo esc_attr( $o['timeout'] ); ?>" />
						</td>
					</tr>

					<tr>
						<th scope="row">
							<label for="ws-limit"><?php esc_html_e( 'Max results', 'wordswift-search' ); ?></label>
						</th>
						<td>
							<input type="number" id="ws-limit" min="1" max="100"
								name="wordswift_settings[limit]" value="<?php echo esc_attr( $o['limit'] ); ?>" />
						</td>
					</tr>

					<tr>
						<th scope="row"><?php esc_html_e( 'Search behaviour', 'wordswift-search' ); ?></th>
						<td>
							<label>
								<input type="checkbox" name="wordswift_settings[typo_tolerance]" value="1"
									<?php checked( $o['typo_tolerance'], 1 ); ?> />
								<?php esc_html_e( 'Typo tolerance (fuzzy matching)', 'wordswift-search' ); ?>
							</label><br />
							<label>
								<input type="checkbox" name="wordswift_settings[hybrid]" value="1"
									<?php checked( $o['hybrid'], 1 ); ?> />
								<?php esc_html_e( 'Hybrid scoring (BM25 + semantic)', 'wordswift-search' ); ?>
							</label>
							<p class="description">
								<?php
								esc_html_e(
									'Hybrid needs a trained embedding model on the index; without one it degrades to BM25.',
									'wordswift-search'
								);
								?>
							</p>
						</td>
					</tr>
				</table>

				<?php submit_button( __( 'Save Settings', 'wordswift-search' ) ); ?>
			</form>

			<hr />

			<h2><?php esc_html_e( 'Index status', 'wordswift-search' ); ?></h2>
			<div id="ws-status"><em><?php esc_html_e( 'Loading…', 'wordswift-search' ); ?></em></div>

			<h2><?php esc_html_e( 'Resync', 'wordswift-search' ); ?></h2>
			<p class="description">
				<?php
				esc_html_e(
					'Re-indexes every published product. Run this after install, and as a nightly safety net against missed webhooks.',
					'wordswift-search'
				);
				?>
			</p>
			<button type="button" class="button button-primary" id="ws-resync">
				<?php esc_html_e( 'Full Resync', 'wordswift-search' ); ?>
			</button>
			<span id="ws-resync-status"></span>

			<hr />

			<h2><?php esc_html_e( 'Test a query', 'wordswift-search' ); ?></h2>
			<p>
				<input type="text" id="ws-test-q" class="regular-text" placeholder="waterprof jaket" />
				<button type="button" class="button" id="ws-test">
					<?php esc_html_e( 'Search', 'wordswift-search' ); ?>
				</button>
			</p>
			<pre id="ws-test-results" style="max-height:320px;overflow:auto;background:#f6f7f7;padding:12px;"></pre>
		</div>

		<script>
		(function () {
			var nonce = <?php echo wp_json_encode( $nonce ); ?>;
			var ajax  = <?php echo wp_json_encode( admin_url( 'admin-ajax.php' ) ); ?>;

			function get(action, params, cb) {
				var qs = new URLSearchParams(Object.assign({ action: action, nonce: nonce }, params || {}));
				fetch(ajax + '?' + qs.toString(), { credentials: 'same-origin' })
					.then(function (r) { return r.json(); })
					.then(cb)
					.catch(function (e) { cb({ success: false, data: { message: String(e) } }); });
			}

			function loadStatus() {
				var el = document.getElementById('ws-status');
				get('wordswift_test', { q: 'health-check' }, function (res) {
					if (res.success && res.data.health && res.data.health.status) {
						el.innerHTML = '<span style="color:#00a32a;">&#10003;</span> '
							+ 'Connected — ' + JSON.stringify(res.data.health);
					} else {
						el.innerHTML = '<span style="color:#d63638;">&#10007;</span> '
							+ 'Not reachable: ' + (res.data && res.data.message
								? res.data.message : 'unknown error');
					}
				});
			}

			document.getElementById('ws-test').addEventListener('click', function () {
				var q   = document.getElementById('ws-test-q').value.trim();
				var out = document.getElementById('ws-test-results');
				if (!q) { return; }
				out.textContent = 'Searching…';
				get('wordswift_test', { q: q }, function (res) {
					if (!res.success) { out.textContent = 'Error: ' + res.data.message; return; }
					out.textContent = JSON.stringify(res.data.hits || [], null, 2)
						|| 'No hits';
				});
			});

			document.getElementById('ws-resync').addEventListener('click', function () {
				var btn    = this;
				var status = document.getElementById('ws-resync-status');
				btn.disabled = true;
				status.textContent = ' Resyncing… this may take a while for large catalogs.';

				var qs = new URLSearchParams({ action: 'wordswift_resync', nonce: nonce });
				fetch(ajax + '?' + qs.toString(), { credentials: 'same-origin' })
					.then(function (r) { return r.json(); })
					.then(function (res) {
						btn.disabled = false;
						if (res.success) {
							status.innerHTML = ' <span style="color:#00a32a;">&#10003;</span> Indexed '
								+ res.data.indexed + ' products (' + res.data.skipped + ' skipped).';
							loadStatus();
						} else {
							status.innerHTML = ' <span style="color:#d63638;">&#10007;</span> '
								+ res.data.message;
						}
					})
					.catch(function (e) {
						btn.disabled = false;
						status.textContent = ' Failed: ' + e;
					});
			});

			loadStatus();
		})();
		</script>
		<?php
	}
}
