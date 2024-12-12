import asyncio
import logging
from datetime import datetime
import yaml
from telegram import Bot
from telegram.error import TelegramError
from .deals_finder import DealsFinder
from .database import DealsDatabase
from .proxy_manager import ProxyManager

logger = logging.getLogger(__name__)

class DealsBot:
    def __init__(self, config_path: str):
        with open(config_path, 'r') as f:
            self.config = yaml.safe_load(f)
            
        self.bot = Bot(token=self.config['telegram']['bot_token'])
        self.channel_id = self.config['telegram']['channel_id']
        self.db = DealsDatabase(self.config['database']['path'])
        self.proxy_manager = ProxyManager(
            max_proxies=self.config['scraping']['max_proxies']
        )
        self.running = True

    async def initialize(self):
        await self.proxy_manager.initialize()
        self.db.clean_old_deals(self.config['database']['retention_days'])

    async def post_deal(self, deal):
        try:
            message = self._format_deal_message(deal)
            await self.bot.send_message(
                chat_id=self.channel_id,
                text=message,
                disable_web_page_preview=False
            )
            self.db.update_deal(deal)
            return True
        except TelegramError as e:
            logger.error(f"Telegram error: {e}")
            return False
        except Exception as e:
            logger.error(f"Error posting deal: {e}")
            return False

    def _format_deal_message(self, deal):
        stats = self.db.get_deal_stats(deal['asin'])
        
        price_history = ""
        if stats and stats['times_posted'] > 1:
            price_history = (
                f"\n💹 Lowest Price: ${stats['lowest_price']:.2f}\n"
                f"📊 Times Listed: {stats['times_posted']}"
            )

        affiliate_link = self._generate_affiliate_link(deal['url'])
        
        message = (
            f"🔥 HOT DEAL ALERT! 🔥\n\n"
            f"📦 {deal['title']}\n\n"
            f"💰 Sale Price: ${deal['sale_price']:.2f}\n"
            f"❌ Original: ${deal['original_price']:.2f}\n"
            f"💯 Save: {deal['discount']}% "
            f"(${deal['original_price'] - deal['sale_price']:.2f})"
            f"{price_history}\n\n"
            f"🛍️ Buy Now: {affiliate_link}\n\n"
            f"#{deal['category'].capitalize()} #AmazonDeals #Discount\n"
            f"Posted: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        return message

    def _generate_affiliate_link(self, url):
        affiliate_id = self.config['amazon']['affiliate_id']
        if '?' in url:
            return f"{url}&tag={affiliate_id}"
        return f"{url}?tag={affiliate_id}"

    async def run_forever(self):
        logger.info("Starting Deals Bot...")
        await self.initialize()

        while self.running:
            try:
                proxy = await self.proxy_manager.get_working_proxy()
                deals_finder = DealsFinder(
                    session=self.proxy_manager.session,
                    config=self.config
                )
                
                deals = await deals_finder.get_deals()
                logger.info(f"Found {len(deals)} potential deals")
                
                posted_count = 0
                for deal in deals:
                    if not self.running:
                        break
                        
                    if not self.db.is_duplicate_deal(
                        deal['asin'], 
                        deal['sale_price']
                    ):
                        success = await self.post_deal(deal)
                        if success:
                            posted_count += 1
                            await asyncio.sleep(
                                self.config['scraping']['request_delay']['min']
                            )
                
                logger.info(f"Posted {posted_count} new deals")
                
                if self.running:
                    await asyncio.sleep(
                        self.config['scraping']['update_interval_minutes'] * 60
                    )
                    
            except Exception as e:
                logger.error(f"Error in main loop: {e}")
                await asyncio.sleep(60)

        logger.info("Bot shutdown complete.")