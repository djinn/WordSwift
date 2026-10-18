<?php
/**
 * bin/magento wordswift:resync — re-index the whole catalog.
 *
 * @package WordSwift_Search
 */

namespace WordSwift\Search\Console\Command;

use Magento\Framework\App\Area;
use Magento\Framework\App\State as AppState;
use Symfony\Component\Console\Command\Command;
use Symfony\Component\Console\Input\InputInterface;
use Symfony\Component\Console\Input\InputOption;
use Symfony\Component\Console\Output\OutputInterface;
use WordSwift\Search\Model\Config;
use WordSwift\Search\Model\Sync;

class ResyncCommand extends Command
{
    const NAME = 'wordswift:resync';

    /** @var Sync */
    private $sync;

    /** @var Config */
    private $config;

    /** @var AppState */
    private $appState;

    public function __construct(
        Sync $sync,
        Config $config,
        AppState $appState,
        string $name = null
    ) {
        $this->sync = $sync;
        $this->config = $config;
        $this->appState = $appState;
        parent::__construct($name);
    }

    protected function configure()
    {
        $this->setName(self::NAME)
            ->setDescription('Re-index all catalog products into WordSwift')
            ->addOption(
                'batch-size',
                'b',
                InputOption::VALUE_REQUIRED,
                'Products per batch',
                100
            );

        parent::configure();
    }

    protected function execute(InputInterface $input, OutputInterface $output)
    {
        // Run in the admin area so store-scoped attributes and URLs resolve.
        return $this->appState->emulateAreaCode(Area::AREA_ADMINHTML, function () use ($input, $output) {
            $batchSize = (int)$input->getOption('batch-size');
            if ($batchSize < 1) {
                $output->writeln('<error>batch-size must be at least 1</error>');
                return Command::FAILURE;
            }

            $url = $this->config->getUrl();
            $index = $this->config->getIndex();

            $output->writeln(sprintf('<info>WordSwift:</info> %s', $url));
            $output->writeln(sprintf('<info>Index:</info> %s', $index));

            $output->writeln('<info>Resyncing catalog…</info>');
            $started = microtime(true);

            $result = $this->sync->fullResync($batchSize);

            $elapsed = microtime(true) - $started;

            if (isset($result['error'])) {
                $output->writeln(sprintf('<error>Failed: %s</error>', $result['error']));
                $output->writeln('Is the WordSwift server running? Try: docker compose up -d');
                return Command::FAILURE;
            }

            $output->writeln(sprintf(
                '<info>Done in %.1fs.</info> indexed=%d skipped=%d',
                $elapsed,
                $result['indexed'],
                $result['skipped']
            ));

            return Command::SUCCESS;
        });
    }
}
