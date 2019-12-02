from datetime import datetime
from bson.objectid import ObjectId
from hashlib import md5

import scrapy
from scrapy.http import FormRequest
from scrapy.loader import ItemLoader
from scrapy_splash import SplashRequest

from soccer_stats.items import (
    League,
    Match,
    PostMatchStatistics
)


class FootyStatsSpider(scrapy.Spider):
    """
    Spider that pulls soccer data from web resources.
    Fetched data: leagues and related matches.
    """

    name = 'footy_stats_spider'
    allowed_domains = ['footystats.org']
    base_url = 'https://footystats.org'

    custom_settings = {'LOG_STDOUT': True, 'LOG_FILE': None}

    match_wait_script = """
        function main(splash)
            splash.images_enabled = false
            assert(splash:go(splash.args.url))
            
            while not splash:select('p[data-time]') do
                splash:wait(1)
            end

            return {html = splash:html()}
        end
    """

    def start_requests(self):
        yield scrapy.Request(
            url=self.make_url('/leagues/'),
            callback=self.parse_leagues
        )

    def parse_leagues(self, response):
        """Parses response and fetches country selectors from content. Gets
        coutry name from selector and than yields requests for all leagues urls
        for each available country."""

        country_selectors = response.xpath('//div[@class="pt2e"]')

        for selector in country_selectors:
            country = selector.attrib['id']  # country name
            leagues_urls = selector.xpath('div/table/tr/td/a/@href').getall()

            for url in leagues_urls:

                yield response.follow(
                    url=url,
                    callback=self.parse_league,
                    cb_kwargs={'country': country}
                )

    def parse_league(self, response, **kwargs):
        """Makes requests for each available season for particular league."""        

        seasons = response.xpath(
            '//div[@class="detail season"]/'
            'div[contains(@class, "drop-down-parent")]/ul/li/a'
        )

        for season in seasons:
            
            # parameters for urls query string
            params = {
                'hash': season.attrib['data-hash'],
                'zzz': season.attrib['data-zzz'],
                'zzzz': season.attrib['data-zzzz'],
                'cur': season.attrib['data-z']
            }

            yield FormRequest(
                url=self.make_url('ajax_league.php'),
                method='POST',
                formdata=params,
                callback=self.parse_season,
                cb_kwargs={'country': kwargs.get('country')}
            )

    def parse_season(self, response, **kwargs):
        """Parses page with particular league season. Creates `league` item and
        fills it with data parsed from returned content. League item has `hash`
        field that uses to prevent duplicate values in a database and for
        manual references with `match` items."""

        loader = ItemLoader(
            item=League(),
            response=response
        )

        loader.add_value('country', kwargs.get('country'))
        loader.add_xpath(
            'title',
            '//div[@id="teamSummary"]/h1[contains(@class, "teamName")]/text()'
        )
        loader.add_xpath(
            'nation',
            (
                '//div[@class="league-details"]/div[@class="detail"]'
                '/div[contains(., "Nation")]/following-sibling::div/a/text()'
            )
        )
        loader.add_xpath(
            'division',
            (
                '//div[@class="league-details"]/div[@class="detail"]'
                '/div[contains(., "Division")]/following-sibling::div/text()'
            )
        )
        loader.add_xpath(
            'league_type',
            (
                '//div[@class="league-details"]/div[@class="detail"]'
                '/div[contains(., "Type")]/following-sibling::div/text()'
            )
        )
        loader.add_xpath(
            'teams_count',
            (
                '//div[@class="league-details"]/div[@class="detail"]'
                '/div[contains(., "Teams")]/following-sibling::div/text()'
            )
        )
        loader.add_xpath(
            'season',
            (
                '//div[@class="league-details"]/div[@class="detail season"]'
                '/div[contains(., "Season")]/following-sibling::div/text()'
            )
        )
        loader.add_xpath(
            'all_matches_count',
            (
                '//div[@class="league-details"]/div[@class="detail season"]'
                '/div[contains(., "Matches")]/following-sibling::div/text()'
            )
        )
        loader.add_xpath(
            'image_url',
            '//div[@id="teamSummary"]/img/@src'
        )

        league = loader.load_item()
        league['hash'] = md5(
            str([league['title'], league['season']]).encode()
        ).hexdigest()

        yield league

        matches = response.xpath(
            '//div[@id="teamSummary"]/ul[contains(@class, "secondary-nav")]/'
            'li[contains(@class, "middle")]/a'
        )  # selector

        href = matches.xpath('@href').get()
        if href == '#':

            # parameters for urls query string
            params = {
                'hash': matches.attrib['data-hash'],
                'zzz': matches.attrib['data-zzz'],
                'cur': matches.attrib['data-z']
            }

            yield FormRequest(
                url=self.make_url('ajax_league.php'),
                method='POST',
                formdata=params,
                callback=self.parse_matches,
                cb_kwargs={'league_hash': league['hash']}
            )

        elif not href:
            # matches for this league available for premium account
            league['blocked'] = True
            yield league

        else:
            yield response.follow(
                url=href,
                callback=self.parse_matches,
                cb_kwargs={'league_hash': league['hash']}
            )

    def parse_matches(self, response, **kwargs):
        """Pulls all urls for each match from returned content and
        yields request for each url."""

        matches = response.xpath(
            '//table[contains(@class, "matches-table")]/tr/'
            'td[contains(@class, "link")]/a/@href'
        ).getall()

        for match in matches:

            yield SplashRequest(
                url=self.make_url(match),
                callback=self.parse_match,
                # endpoint='execute',
                # args={'timeout': 600, 'lua_source': self.match_wait_script},
                args={'wait': 2},
                cb_kwargs={'league_hash': kwargs.get('league_hash')}
            )
        
    def parse_match(self, response, **kwargs):
        """Fetches data about particular event from returned content.
        Creates match item and fills with fetched data. Match item has `hash`
        field that uses to prevent duplicate values in a database and to connect
        it with `PostMatchStatisticsItem` if these data will exist."""

        match_loader = ItemLoader(
            item=Match(),
            response=response
        )

        match_loader.add_value(
            'league_hash',
            kwargs.get('league_hash')
        )
        match_loader.add_xpath(
            'timestamp',
            '//p[@data-time]/@data-time'
        )
        match_loader.add_xpath(
            'home_team',
            '//span[@itemprop="homeTeam"]/span[@itemprop="name"]/@content'
        )
        match_loader.add_xpath(
            'away_team',
            '//span[@itemprop="awayTeam"]/span[@itemprop="name"]/@content'
        )
        match_loader.add_xpath(
            'stadium',
            '//small/span[@itemprop="name"]/text()'
        )
        match_loader.add_xpath(
            'home_result',
            '//div[contains(@class, "h2h-final-score")]/'
            'div[@class="widget-content"]/h2/text()'
        )
        match_loader.add_xpath(
            'away_result',
            '//div[contains(@class, "h2h-final-score")]/'
            'div[@class="widget-content"]/h2/text()'
        )
        match = match_loader.load_item()

        fields = [
            match['league_hash'],
            match['timestamp'],
            match['home_team'],
            match['away_team']
        ]

        match['hash'] = md5(str(fields).encode()).hexdigest()
        yield match

        if response.xpath('//div[@class="w100 cf ac"]'):
            
            # if post match statistics data exists
            statistics_loader = ItemLoader(
                item=PostMatchStatistics(),
                response=response
            )

            statistics_loader.add_value('match_hash', match['hash'])
            statistics_loader.add_xpath(
                'possession',
                '//span[contains(@class, "possession")]/text()'
            )
            statistics_loader.add_xpath(
                'shots',
                '//div[@class="w100 m0Auto"]/div/div[contains(text(), "Shots")]'
                '/following-sibling::div[contains(@class, "bbox")]/span/text()'
            )
            statistics_loader.add_xpath(
                'cards',
                '//div[@class="w100 m0Auto"]/div/div[contains(text(), "Cards")]'
                '/following-sibling::div[contains(@class, "bbox")]/span/text()'
            )
            statistics_loader.add_xpath(
                'corners',
                '//div[@class="w100 m0Auto"]/div/div[contains(text(), "Corners")]'
                '/following-sibling::div[contains(@class, "bbox")]/span/text()'
            )
            statistics_loader.add_xpath(
                'fouls',
                '//div[@class="w100 m0Auto"]/div/div[contains(text(), "Fouls")]'
                '/following-sibling::div[contains(@class, "bbox")]/span/text()'
            )
            statistics_loader.add_xpath(
                'offsides',
                '//div[@class="w100 m0Auto"]/div/div[contains(text(), "Offsides")]'
                '/following-sibling::div[contains(@class, "bbox")]/span/text()'
            )

            yield statistics_loader.load_item()

    def make_url(self, path):
        """Returns url created from base url and relative path."""
        return '{base}/{relative}'.format(base=self.base_url, relative=path)


