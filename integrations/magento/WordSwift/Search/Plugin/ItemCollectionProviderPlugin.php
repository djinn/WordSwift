<?php
/**
 * Swap the catalog search collection for WordSwift-ranked product ids.
 *
 * `ItemCollectionProvider::getCollection()` is the version-stable seam between
 * Magento's search query and the catalog collection. We let Magento build the
 * collection normally, then re-order it by the id sequence WordSwift returned
 * and drop anything WordSwift did not match.
 *
 * If WordSwift is unreachable, disabled, or returns nothing, we return the
 * original collection untouched — the storefront degrades to native search
 * instead of showing an error or an empty page.
 *
 * @package WordSwift_Search
 */

namespace WordSwift\Search\Plugin;

use Magento\Catalog\Model\ResourceModel\Product\Collection;
use Magento\CatalogSearch\Model\Search\ItemCollectionProvider;
use Magento\Framework\DB\Select;
use Magento\Search\Model\QueryInterface;
use Psr\Log\LoggerInterface;
use WordSwift\Search\Api\ClientInterface;
use WordSwift\Search\Model\Config;

class ItemCollectionProviderPlugin
{
    /** @var ClientInterface */
    private $client;

    /** @var Config */
    private $config;

    /** @var LoggerInterface */
    private $logger;

    public function __construct(
        ClientInterface $client,
        Config $config,
        LoggerInterface $logger
    ) {
        $this->client = $client;
        $this->config = $config;
        $this->logger = $logger;
    }

    /**
     * Re-rank the search collection using WordSwift relevance.
     *
     * @param ItemCollectionProvider $subject
     * @param Collection|null $collection
     * @param QueryInterface $query
     * @return Collection|null
     */
    public function afterGetCollection(
        ItemCollectionProvider $subject,
        $collection,
        QueryInterface $query
    ) {
        if (!$this->config->isEnabled()) {
            return $collection;
        }

        if (!$collection instanceof Collection) {
            return $collection;
        }

        $searchText = trim((string)$query->getQueryText());
        if ($searchText === '') {
            return $collection;
        }

        $hits = $this->client->search($searchText, $this->config->getLimit());

        // No results or a failed request → keep native behaviour.
        if (empty($hits)) {
            return $collection;
        }

        $ids = [];
        foreach ($hits as $hit) {
            $id = isset($hit['id']) ? (int)$hit['id'] : 0;
            if ($id > 0) {
                $ids[] = $id;
            }
        }

        if (empty($ids)) {
            return $collection;
        }

        // Restrict to the matched products...
        $collection->addIdFilter($ids);

        // ...and preserve WordSwift's ranking. Without FIELD() ordering MySQL
        // returns rows in primary-key order and the BM25/fuzzy ranking is lost.
        $quoted = array_map(static function ($id) {
            return (string)$id;
        }, $ids);

        $collection->getSelect()->reset(Select::ORDER)
            ->order(new \Zend_Db_Expr('FIELD(e.entity_id, ' . implode(',', $quoted) . ')'));

        return $collection;
    }
}
