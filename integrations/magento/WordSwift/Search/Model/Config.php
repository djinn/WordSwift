<?php
/**
 * Typed access to the WordSwift admin configuration.
 *
 * @package WordSwift_Search
 */

namespace WordSwift\Search\Model;

use Magento\Framework\App\Config\ScopeConfigInterface;
use Magento\Store\Model\ScopeInterface;

class Config
{
    const XML_PATH_PREFIX = 'wordswift/general/';

    /** @var ScopeConfigInterface */
    private $scopeConfig;

    public function __construct(ScopeConfigInterface $scopeConfig)
    {
        $this->scopeConfig = $scopeConfig;
    }

    /**
     * Read one setting, optionally scoped to a store/website.
     *
     * @param string $key
     * @param mixed $default
     * @param string|null $scopeCode
     * @return mixed
     */
    private function get($key, $default = null, $scopeCode = null)
    {
        $value = $this->scopeConfig->getValue(
            self::XML_PATH_PREFIX . $key,
            ScopeInterface::SCOPE_STORES,
            $scopeCode
        );

        return ($value === null || $value === '') ? $default : $value;
    }

    /**
     * Whether WordSwift should handle search at all.
     *
     * @param string|null $scopeCode
     * @return bool
     */
    public function isEnabled($scopeCode = null): bool
    {
        return $this->scopeConfig->isSetFlag(
            self::XML_PATH_PREFIX . 'enabled',
            ScopeInterface::SCOPE_STORES,
            $scopeCode
        );
    }

    /**
     * Base URL with no trailing slash.
     *
     * @param string|null $scopeCode
     * @return string
     */
    public function getUrl($scopeCode = null): string
    {
        $url = (string)$this->get('url', 'http://localhost:7700', $scopeCode);
        return rtrim($url, '/');
    }

    /**
     * @param string|null $scopeCode
     * @return string
     */
    public function getIndex($scopeCode = null): string
    {
        return (string)$this->get('index', 'magento_products', $scopeCode);
    }

    /**
     * @param string|null $scopeCode
     * @return float
     */
    public function getTimeout($scopeCode = null): float
    {
        $timeout = (float)$this->get('timeout', 10, $scopeCode);
        return $timeout > 0 ? min($timeout, 60) : 10.0;
    }

    /**
     * @param string|null $scopeCode
     * @return int
     */
    public function getLimit($scopeCode = null): int
    {
        $limit = (int)$this->get('limit', 20, $scopeCode);
        return $limit > 0 ? min($limit, 100) : 20;
    }

    /**
     * @param string|null $scopeCode
     * @return bool
     */
    public function isHybrid($scopeCode = null): bool
    {
        return (bool)$this->get('hybrid', true, $scopeCode);
    }

    /**
     * @param string|null $scopeCode
     * @return bool
     */
    public function hasTypoTolerance($scopeCode = null): bool
    {
        return (bool)$this->get('typo_tolerance', true, $scopeCode);
    }
}
