<?php
/**
 * Contract for the WordSwift HTTP client.
 *
 * @package WordSwift_Search
 */

namespace WordSwift\Search\Api;

interface ClientInterface
{
    /**
     * Ping the server.
     *
     * @return array|null Null when unreachable.
     */
    public function health();

    /**
     * Create the configured index if absent.
     *
     * @param int $dim
     * @return bool
     */
    public function ensureIndex($dim = 100);

    /**
     * Index or update documents in batches.
     *
     * @param array $documents
     * @param int $batchSize
     * @return int Number of documents accepted.
     */
    public function addDocuments(array $documents, $batchSize = 200);

    /**
     * Search the index.
     *
     * @param string $query
     * @param int $limit
     * @return array List of hits (synthetic _semantic entries removed).
     */
    public function search($query, $limit = 20);

    /**
     * Index statistics.
     *
     * @return array|null
     */
    public function stats();
}
