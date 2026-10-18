<?php
/**
 * WordSwift HTTP client for Magento.
 *
 * Uses Magento's GuzzleHttp (bundled with core) so it respects the admin's
 * proxy and SSL configuration. Every failure is caught and logged — search must
 * never 500 the storefront, so callers receive empty arrays and fall back to
 * native Magento search.
 *
 * @package WordSwift_Search
 */

namespace WordSwift\Search\Model;

use GuzzleHttp\Client as HttpClient;
use GuzzleHttp\Exception\GuzzleException;
use GuzzleHttp\RequestOptions;
use Psr\Log\LoggerInterface;
use WordSwift\Search\Api\ClientInterface;

class Client implements ClientInterface
{
    /** @var Config */
    private $config;

    /** @var LoggerInterface */
    private $logger;

    /** @var HttpClient|null */
    private $http;

    public function __construct(
        Config $config,
        LoggerInterface $logger
    ) {
        $this->config = $config;
        $this->logger = $logger;
    }

    /**
     * Lazily build the HTTP client so configuration changes take effect.
     */
    private function http(): HttpClient
    {
        if ($this->http === null) {
            $this->http = new HttpClient([
                'base_uri' => $this->config->getUrl(),
                RequestOptions::TIMEOUT => $this->config->getTimeout(),
                RequestOptions::CONNECT_TIMEOUT => min(5, $this->config->getTimeout()),
                RequestOptions::HTTP_ERRORS => false, // we inspect status codes ourselves
                RequestOptions::HEADERS => [
                    'Accept' => 'application/json',
                    'User-Agent' => 'WordSwift-Magento/1.0',
                ],
            ]);
        }

        return $this->http;
    }

    /**
     * Perform a request and decode the JSON body.
     *
     * @param string $method
     * @param string $path
     * @param mixed|null $body
     * @return array|null Decoded payload, or null on any failure.
     */
    private function request(string $method, string $path, $body = null)
    {
        try {
            $options = [];
            if ($body !== null) {
                $options[RequestOptions::JSON] = $body;
            }

            $response = $this->http()->request($method, $path, $options);
            $status = $response->getStatusCode();
            $raw = (string)$response->getBody();

            if ($status >= 400) {
                $this->logger->warning(
                    sprintf('[WordSwift] %s %s → HTTP %d: %s', $method, $path, $status, substr($raw, 0, 200))
                );
                return null;
            }

            if ($raw === '') {
                return [];
            }

            $decoded = json_decode($raw, true);

            return is_array($decoded) ? $decoded : [];
        } catch (GuzzleException $e) {
            $this->logger->warning('[WordSwift] request failed: ' . $e->getMessage());
            return null;
        } catch (\Throwable $e) {
            $this->logger->critical('[WordSwift] unexpected error: ' . $e->getMessage());
            return null;
        }
    }

    /**
     * @inheritDoc
     */
    public function health()
    {
        return $this->request('GET', '/health');
    }

    /**
     * @inheritDoc
     */
    public function ensureIndex($dim = 100)
    {
        $index = $this->config->getIndex();

        $result = $this->request('POST', '/indexes', [
            'uid' => $index,
            'dim' => (int)$dim,
        ]);

        // A 400 "already exists" is success for our purposes; request() returns
        // null for it, so verify by reading the index back.
        if ($result === null) {
            $check = $this->request('GET', '/indexes/' . rawurlencode($index));
            return $check !== null;
        }

        return true;
    }

    /**
     * @inheritDoc
     */
    public function addDocuments(array $documents, $batchSize = 200)
    {
        if (empty($documents)) {
            return 0;
        }

        $path = '/indexes/' . rawurlencode($this->config->getIndex()) . '/documents';
        $added = 0;

        foreach (array_chunk($documents, max(1, (int)$batchSize)) as $batch) {
            $result = $this->request('POST', $path, $batch);
            if ($result === null) {
                $this->logger->error(
                    sprintf('[WordSwift] failed to index a batch of %d documents', count($batch))
                );
                continue; // keep going — partial indexing beats none
            }
            $added += isset($result['documentsCount']) ? (int)$result['documentsCount'] : 0;
        }

        return $added;
    }

    /**
     * @inheritDoc
     */
    public function search($query, $limit = 20)
    {
        $query = trim((string)$query);
        if ($query === '') {
            return [];
        }

        $path = '/indexes/' . rawurlencode($this->config->getIndex()) . '/search?' . http_build_query([
            'q' => $query,
            'limit' => (int)$limit,
            'hybrid' => $this->config->isHybrid() ? 'true' : 'false',
            'typo_tolerance' => $this->config->hasTypoTolerance() ? 'true' : 'false',
            'prefix_search' => 'true',
        ]);

        $result = $this->request('GET', $path);
        if ($result === null || !isset($result['hits']) || !is_array($result['hits'])) {
            return [];
        }

        // Drop synthetic vocabulary hits (negative ids) emitted when WordSwift
        // falls back to its semantic stage — they are not real products.
        $hits = [];
        foreach ($result['hits'] as $hit) {
            if (!empty($hit['_semantic'])) {
                continue;
            }
            $hits[] = $hit;
        }

        return $hits;
    }

    /**
     * @inheritDoc
     */
    public function stats()
    {
        return $this->request('GET', '/indexes/' . rawurlencode($this->config->getIndex()) . '/stats');
    }
}
