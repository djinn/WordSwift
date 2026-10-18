<?php
/**
 * Re-index a single product whenever it is saved or deleted.
 *
 * Indexing runs synchronously on the save event, which adds one HTTP round-trip
 * to every product save. That is fine for modest catalogs; for very large or
 * high-churn catalogs move this to an async queue or a cron resync instead.
 *
 * Failures are logged and swallowed — a WordSwift outage must never prevent a
 * merchant from saving a product.
 *
 * @package WordSwift_Search
 */

namespace WordSwift\Search\Observer;

use Magento\Framework\Event\Observer;
use Magento\Framework\Event\ObserverInterface;
use Psr\Log\LoggerInterface;
use WordSwift\Search\Model\Config;
use WordSwift\Search\Model\Sync;

class ProductSaveObserver implements ObserverInterface
{
    /** @var Sync */
    private $sync;

    /** @var Config */
    private $config;

    /** @var LoggerInterface */
    private $logger;

    public function __construct(
        Sync $sync,
        Config $config,
        LoggerInterface $logger
    ) {
        $this->sync = $sync;
        $this->config = $config;
        $this->logger = $logger;
    }

    /**
     * @param Observer $observer
     * @return void
     */
    public function execute(Observer $observer)
    {
        if (!$this->config->isEnabled()) {
            return;
        }

        try {
            $product = $observer->getEvent()->getProduct();
            if ($product === null) {
                return;
            }

            $productId = (int)$product->getId();
            if ($productId <= 0) {
                return;
            }

            $this->sync->indexProduct($productId);
        } catch (\Throwable $e) {
            // Never block the save operation because indexing failed.
            $this->logger->error('[WordSwift] product save indexing failed: ' . $e->getMessage());
        }
    }
}
