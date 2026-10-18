<?php
/**
 * Catalog → WordSwift sync.
 *
 * Builds one document per product and pushes it to the index. Only string
 * fields are indexed by WordSwift, so every value is cast to string; the id
 * stays an integer because the registry coerces it to int and falls back to an
 * auto-increment counter for non-numeric strings.
 *
 * @package WordSwift_Search
 */

namespace WordSwift\Search\Model;

use Magento\Catalog\Api\Data\ProductInterface;
use Magento\Catalog\Api\ProductRepositoryInterface;
use Magento\Catalog\Model\ResourceModel\Product\CollectionFactory as ProductCollectionFactory;
use Magento\CatalogInventory\Api\StockRegistryInterface;
use Magento\Framework\Exception\NoSuchEntityException;
use Magento\Framework\UrlInterface;
use Psr\Log\LoggerInterface;
use WordSwift\Search\Api\ClientInterface;

class Sync
{
    /** @var ClientInterface */
    private $client;

    /** @var Config */
    private $config;

    /** @var ProductCollectionFactory */
    private $collectionFactory;

    /** @var ProductRepositoryInterface */
    private $productRepository;

    /** @var StockRegistryInterface */
    private $stockRegistry;

    /** @var UrlInterface */
    private $urlBuilder;

    /** @var LoggerInterface */
    private $logger;

    public function __construct(
        ClientInterface $client,
        Config $config,
        ProductCollectionFactory $collectionFactory,
        ProductRepositoryInterface $productRepository,
        StockRegistryInterface $stockRegistry,
        UrlInterface $urlBuilder,
        LoggerInterface $logger
    ) {
        $this->client = $client;
        $this->config = $config;
        $this->collectionFactory = $collectionFactory;
        $this->productRepository = $productRepository;
        $this->stockRegistry = $stockRegistry;
        $this->urlBuilder = $urlBuilder;
        $this->logger = $logger;
    }

    /**
     * Build the WordSwift document for a product.
     *
     * @param ProductInterface $product
     * @return array|null Null when the product should not be searchable.
     */
    public function buildDocument(ProductInterface $product)
    {
        // Disabled products and those hidden from search/catalog must not appear.
        if ((int)$product->getStatus() !== \Magento\Catalog\Model\Product\Attribute\Source\Status::STATUS_ENABLED) {
            return null;
        }

        $visibility = (int)$product->getVisibility();
        $searchable = [
            \Magento\Catalog\Model\Product\Visibility::VISIBILITY_IN_CATALOG,
            \Magento\Catalog\Model\Product\Visibility::VISIBILITY_IN_SEARCH,
            \Magento\Catalog\Model\Product\Visibility::VISIBILITY_BOTH,
        ];
        if (!in_array($visibility, $searchable, true)) {
            return null;
        }

        $id = (int)$product->getId();
        if ($id <= 0) {
            return null;
        }

        $description = $this->plainText($product->getDescription());
        $shortDescription = $this->plainText($product->getShortDescription());

        $inStock = true;
        try {
            $stockItem = $this->stockRegistry->getStockItem($id);
            $inStock = (bool)$stockItem->getIsInStock();
        } catch (NoSuchEntityException $e) {
            // No stock record — treat as in stock rather than hiding the product.
            $inStock = true;
        }

        $doc = [
            'id' => $id, // integer on purpose
            'title' => (string)$product->getName(),
            'sku' => (string)$product->getSku(),
            'description' => $description,
            'short_description' => $shortDescription,
            'price' => $this->num($product->getPrice()),
            'special_price' => $this->num($product->getSpecialPrice()),
            'brand' => $this->attributeLabel($product, 'manufacturer'),
            'color' => $this->attributeLabel($product, 'color'),
            'material' => $this->attributeLabel($product, 'material'),
            'url' => $this->productUrl($product),
            'in_stock' => $inStock ? 'yes' : 'no',
            'meta_keyword' => (string)$product->getMetaKeyword(),
            'meta_description' => (string)$product->getMetaDescription(),
        ];

        // Category path is a strong faceting/search signal.
        $categories = [];
        foreach ((array)$product->getCategoryIds() as $categoryId) {
            $categories[] = (string)$categoryId;
        }
        if ($categories) {
            $doc['category_ids'] = implode(', ', $categories);
        }

        // Drop empty values so the index stays lean.
        return array_filter($doc, static function ($value) {
            return $value !== null && $value !== '';
        });
    }

    /**
     * Strip HTML to plain prose for BM25 tokenisation.
     *
     * @param string|null $html
     * @return string
     */
    private function plainText($html): string
    {
        if (!$html) {
            return '';
        }

        $text = strip_tags((string)$html);
        $text = html_entity_decode($text, ENT_QUOTES | ENT_HTML5, 'UTF-8');
        $text = preg_replace('/\s+/u', ' ', $text);

        return trim((string)$text);
    }

    /**
     * Format a numeric attribute as a stable string.
     *
     * @param mixed $value
     * @return string
     */
    private function num($value): string
    {
        if ($value === null || $value === '' || $value === false) {
            return '';
        }

        return (string)$value;
    }

    /**
     * Resolve an attribute's display label rather than its raw option id.
     *
     * @param ProductInterface $product
     * @param string $code
     * @return string
     */
    private function attributeLabel(ProductInterface $product, $code): string
    {
        try {
            $attribute = $product->getResource()->getAttribute($code);
            if ($attribute === null) {
                return '';
            }

            $frontend = $attribute->getFrontend();
            $value = $frontend ? (string)$frontend->getValue($product) : '';

            // "No" / "N/A" placeholders carry no search value.
            if ($value === '' || $value === 'No' || $value === 'N/A') {
                return '';
            }

            return $value;
        } catch (\Throwable $e) {
            return '';
        }
    }

    /**
     * Build the storefront URL, preferring the product's own url_key.
     *
     * @param ProductInterface $product
     * @return string
     */
    private function productUrl(ProductInterface $product): string
    {
        try {
            $urlKey = $product->getUrlKey();
            if ($urlKey) {
                return (string)$this->urlBuilder->getUrl('', ['_direct' => $urlKey . '.html']);
            }

            return (string)$product->getProductUrl();
        } catch (\Throwable $e) {
            return '';
        }
    }

    /**
     * Index or re-index a single product.
     *
     * @param int $productId
     * @return bool
     */
    public function indexProduct($productId): bool
    {
        try {
            $product = $this->productRepository->getById((int)$productId);
        } catch (NoSuchEntityException $e) {
            $this->logger->info("[WordSwift] product {$productId} no longer exists");
            return false;
        } catch (\Throwable $e) {
            $this->logger->error('[WordSwift] could not load product: ' . $e->getMessage());
            return false;
        }

        $doc = $this->buildDocument($product);
        if ($doc === null) {
            // Not searchable any more. WordSwift has no per-document delete, so
            // it lingers until the next full resync — see docs/ECOMMERCE.md.
            $this->logger->info(
                "[WordSwift] product {$productId} is not searchable; it will drop out on the next resync"
            );
            return false;
        }

        if (!$this->client->ensureIndex()) {
            return false;
        }

        return $this->client->addDocuments([$doc]) > 0;
    }

    /**
     * Re-index the whole catalog in batches.
     *
     * @param int $batchSize
     * @return array Summary with indexed/skipped counts.
     */
    public function fullResync($batchSize = 100): array
    {
        if (!$this->client->ensureIndex()) {
            return ['indexed' => 0, 'skipped' => 0, 'error' => 'cannot reach or create the index'];
        }

        $batchSize = max(1, (int)$batchSize);
        $indexed = 0;
        $skipped = 0;
        $page = 1;

        do {
            $collection = $this->collectionFactory->create();
            $collection->addAttributeToSelect('*')
                ->setPageSize($batchSize)
                ->setCurPage($page)
                ->addStoreFilter()
                ->addAttributeToSort('entity_id', 'ASC');

            $totalPages = (int)$collection->getLastPageNumber();
            $documents = [];

            foreach ($collection as $product) {
                $doc = $this->buildDocument($product);
                if ($doc === null) {
                    $skipped++;
                    continue;
                }
                $documents[] = $doc;
            }

            if ($documents) {
                $indexed += $this->client->addDocuments($documents, $batchSize);
            }

            $page++;
        } while ($page <= $totalPages && $totalPages > 0);

        return ['indexed' => $indexed, 'skipped' => $skipped];
    }
}
