<?php
/**
 * bin/magento wordswift:search "query" — run a search from the CLI.
 *
 * Useful for verifying an index without loading the storefront, and for
 * reproducing typo-tolerant queries from a bug report.
 *
 * @package WordSwift_Search
 */

namespace WordSwift\Search\Console\Command;

use Symfony\Component\Console\Command\Command;
use Symfony\Component\Console\Input\InputArgument;
use Symfony\Component\Console\Input\InputInterface;
use Symfony\Component\Console\Input\InputOption;
use Symfony\Component\Console\Output\OutputInterface;
use WordSwift\Search\Api\ClientInterface;
use WordSwift\Search\Model\Config;

class TestSearchCommand extends Command
{
    const NAME = 'wordswift:search';

    /** @var ClientInterface */
    private $client;

    /** @var Config */
    private $config;

    public function __construct(
        ClientInterface $client,
        Config $config,
        string $name = null
    ) {
        $this->client = $client;
        $this->config = $config;
        parent::__construct($name);
    }

    protected function configure()
    {
        $this->setName(self::NAME)
            ->setDescription('Run a WordSwift search query from the CLI')
            ->addArgument('query', InputArgument::REQUIRED, 'Search text')
            ->addOption('limit', 'l', InputOption::VALUE_REQUIRED, 'Max results', 10)
            ->addOption('stats', null, InputOption::VALUE_NONE, 'Show index stats instead of searching');

        parent::configure();
    }

    protected function execute(InputInterface $input, OutputInterface $output)
    {
        $url = $this->config->getUrl();
        $index = $this->config->getIndex();

        $output->writeln(sprintf('<info>WordSwift:</info> %s', $url));
        $output->writeln(sprintf('<info>Index:</info> %s', $index));

        // Reachability check first, so the failure mode is obvious.
        $health = $this->client->health();
        if ($health === null) {
            $output->writeln('<error>Cannot reach WordSwift. Is it running? (docker compose up -d)</error>');
            return Command::FAILURE;
        }
        $output->writeln(sprintf('<info>Health:</info> %s', json_encode($health)));

        if ($input->getOption('stats')) {
            $stats = $this->client->stats();
            if ($stats === null) {
                $output->writeln(sprintf(
                    '<error>Index "%s" not found. Run: bin/magento wordswift:resync</error>',
                    $index
                ));
                return Command::FAILURE;
            }
            $output->writeln('<info>Stats:</info>');
            // fullTextDocuments is the real product count; numberOfDocuments is
            // embedding vocab size and stays 0 until a model is trained.
            $output->writeln(sprintf('  products (fullTextDocuments): %s', $stats['fullTextDocuments'] ?? '0'));
            $output->writeln(sprintf('  vocab (numberOfDocuments):      %s', $stats['numberOfDocuments'] ?? '0'));
            $output->writeln(sprintf('  dimension:                      %s', $stats['dimension'] ?? '?'));
            return Command::SUCCESS;
        }

        $query = (string)$input->getArgument('query');
        $limit = max(1, (int)$input->getOption('limit'));

        $hits = $this->client->search($query, $limit);

        if (empty($hits)) {
            $output->writeln(sprintf('<comment>No results for "%s"</comment>', $query));
            return Command::SUCCESS;
        }

        $output->writeln(sprintf('<info>%d result(s) for "%s":</info>', count($hits), $query));
        $output->writeln('');
        foreach ($hits as $i => $hit) {
            $output->writeln(sprintf(
                '  %2d. %8s  [id=%s] %s',
                $i + 1,
                number_format((float)($hit['score'] ?? 0), 4, '.', ''),
                $hit['id'] ?? '?',
                $hit['title'] ?? '(untitled)'
            ));
        }

        return Command::SUCCESS;
    }
}
