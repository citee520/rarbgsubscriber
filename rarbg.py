import os
import re
import sqlite3
import logging
import logging.config
import logging.handlers
import sys
import json
import os
import datetime
import urlparse

import requests
import lxml
from bs4 import BeautifulSoup
from guessit import guessit

from .pool import (MoviePool, MovieInfo)
from .handler import HandlerManager
from .filter import Filter


LOG = logging.getLogger()


class Rarbg(object):
    def __init__(self):
        # TODO: how to pass bot check
        self._cookie = 'c_cookie=ljd3gcszby; expla2=1%7CSat%2C%2005%20Mar%202016%2021%3A46%3A26%20GMT; LastVisit=1457192974; vDVPaqSe=r9jSB2Wk; expla=2; tcc; __utma=9515318.355702320.1444490883.1457101385.1457192786.76; __utmb=9515318.4.10.1457192786; __utmc=9515318; __utmz=9515318.1447162163.12.4.utmcsr=google|utmccn=(organic)|utmcmd=organic|utmctr=(not%20provided)'
        self._host = 'http://rarbg.to'

    def conn(self, uri, query=None):
        url = urlparse.urljoin(self._host, uri)
        header = {
            'Cookie': self._cookie
        }

        sess = requests.Session()
        resp = sess.get(url, params=query, headers=header)
        resp.raise_for_status()
        return resp


class TorrentListPage(object):
    def __init__(self, host, resp):
        self._host = host
        self._soup = BeautifulSoup(resp.content, 'lxml')

    def _get_imdb(self, tag):
        if not tag:
            return 0.0

        ptr = tag.text.find('IMDB:')
        if ptr < 0:
            return 0.0

        text = tag.text[ptr+len('IMDB: '):]
        return float(text.split('/')[0])

    def _parse(self, tr):
        result = dict()
        _, info_tag, date_tag, size_tag, _, _, _, _ = tr.find_all('td')
        imdb_tag = info_tag.find('span', style=re.compile('color*'))
        result['screen_size'] = "UNKNOWN"
        result['href'] = urlparse.urljoin(self._host, info_tag.a['href'])
        result['imdb'] = self._get_imdb(imdb_tag)
        result['size'] = size_tag.text
        result.update(guessit(info_tag.a['title']))
        return RarbgTorrent(result)

    def __iter__(self):
        lista2t_table = self._soup.find('table', class_='lista2t')
        assert lista2t_table
        for tr in lista2t_table.find_all('tr', class_='lista2'):
            yield self._parse(tr)


class RarbgTorrent(Rarbg, dict):
    def __init__(self, raw):
        super(RarbgTorrent, self).__init__()
        self._raw = raw
        self._page_href = raw['href']
        self._page_soup = None

        self.title = raw['title']
        self.year = raw.get('year', 'UNKNOWN')
        self.resolution = raw['screen_size']
        self.format = raw.get('format', 'UNKNOWN')
        self.size = raw['size']
        self.video_codec = raw.get('video_codec')
        self.audio_codec = raw.get('audio_codec')
        self.imdb = raw['imdb']

    def __str__(self):
        return "%s.%s.%s.%s.%s.%s.%s" % (self.title, self.year,
                                         self.resolution, self.format,
                                         self.video_codec, self.audio_codec,
                                         self.size)

    @property
    def href(self):
        if not self._page_soup:
            resp = self.conn(self._page_href)
            self._page_soup = BeautifulSoup(resp.content, 'lxml')

        a = self._page_soup.find('a', href=re.compile('download.php'))
        return urlparse.urljoin(self._host, a["href"])

    @property
    def cover(self):
        if not self._page_soup:
            resp = self.conn(self._page_href)
            self._soup = BeautifulSoup(resp.content, 'lxml')

        img = self._page_soup.find("img", itemprop="image")
        return urlparse.urljoin(self._host, img['src'])


class RarbgPager(Rarbg):
    def __init__(self, category=None, search=None,
                 start_page=1, end_page=None):
        super(RarbgPager, self).__init__()
        self._uri = "torrents.php"
        self._category = category
        self._search = search
        self._current_index = start_page
        self._max_index = end_page
        self._page = None

    @property
    def page(self):
        return self._page

    def _query(self):
        query = {
            'page': self._current_index
        }
        if self._category:
            query['category'] = self._category

        if self._search:
            query['search'] = self._search

        return self.conn(self._uri, query=query)

    def _get_next_index(self, resp):
        soup = BeautifulSoup(resp.content, 'lxml')
        tag = soup.find('div', id='pager_links')
        assert tag
        b_tag = tag.find('b')
        if not b_tag:
            self._max_index = self._current_index
            return self._max_index
        value = int(b_tag.text)
        next_tag = tag.find('a', title='next page')
        if not next_tag:
            if self._current_index >= value:
                self._max_index = self._current_index
        return value+1

    def __iter__(self):
        return self

    def next(self):
        if self._current_index == self._max_index:
            raise StopIteration()

        LOG.debug("Page %s", self._current_index)
        resp = self._query()
        self._page = TorrentListPage(self._host, resp)
        self._current_index = self._get_next_index(resp)
        return self._page


class RARBGspider(object):
    def __init__(self, conf):
        self._conf = conf
        self._workspace = self.__class__.__name__
        self._debug = False
        self._update_general_settings()
        self._create_workspace()

        self._filter = self._get_filter()
        self._handlers = self._get_handler_manager()
        self._db_conn = self._get_db_connection()
        self._pool = MoviePool(self._db_conn)
        # FIXME: category 44 = 1080p movie
        self._pager = RarbgPager(category=44)
        self._setting_logger()

    def _setting_logger(self):
        log_dict = {
            "version": 1,
            "disable_existing_loggers": not self._debug,
            "root": {
                "level": "NOTSET",
                "handlers": ["console", "file"]
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "level": "DEBUG",
                    "formatter": "precise",
                },
                "file": {
                    "class": "logging.handlers.TimedRotatingFileHandler",
                    "level": "INFO",
                    "filename": "%s/%s.log" % (self._workspace,
                                               datetime.date.today()),
                    "formatter": "detail",
                    "when": "D",
                    "backupCount": 30
                }
            },
            "formatters": {
                "precise": {
                    "format": "%(asctime)s - %(message)s"
                },
                "detail": {
                    "format": "%(asctime)s %(filename)s:%(funcName)s:%(lineno)d - %(message)s"
                }
            },
        }

        logging.config.dictConfig(log_dict)


    def _create_workspace(self):
        path = os.path.abspath(self._workspace)
        if not os.path.exists(path):
            os.mkdir(path)

    def _update_general_settings(self):
        general_conf = self._conf.get('general')
        if not general_conf:
            return

        self._workspace = general_conf.get('workspace',
                                           self.__class__.__name__)
        self._debug = general_conf.get('debug', False)

    def _get_filter(self):
        filter_conf = self._conf.get('filter')
        LOG.info("Filter %s", filter_conf)
        return Filter(filter_conf)

    def _get_handler_manager(self):
        handler_conf = self._conf.get('handlers')
        LOG.info("Handlers %s", handler_conf)
        return HandlerManager(handler_conf)

    def _get_db_connection(self, name="pool"):
        path = os.path.join(self._workspace, name)
        return sqlite3.connect(path)

    def close(self):
        self._db_conn.commit()

    def _convert_to_movie_info(self, torrent):
        info = None
        try:
            info = MovieInfo(torrent._raw)
            info.href = torrent.href
        except Exception as exp:
            LOG.warn("Incomplete torrent %s", torrent)
        return info

    def crawl(self):
        stop = False
        for page in self._pager:
            if stop:
                break

            for torrent in page:
                if torrent is None:
                    continue

                if not self._filter.filter(torrent):
                    LOG.debug("Skip %s", torrent)
                    continue

                info = self._convert_to_movie_info(torrent)
                if self._pool.find(info.href) is not None:
                    # FIXME: ugly
                    stop = True
                    LOG.info("duplication torrent, stop")
                    break

                LOG.info("New torrent %s", info)
                self._pool.insert(info)
                self._handlers.register(info)

        self._handlers.submit()
        LOG.info("Crawl done")


if __name__ == "__main__":
    assert len(sys.argv) == 2

    try:
        conf_path = sys.argv[1]
        with open(conf_path, "rb") as fp:
            conf = fp.read()
            conf_dict = json.loads(conf)
            r = RARBGspider(conf_dict)
            r.crawl()
            r.close()
    except Exception as exp:
        LOG.exception(exp)
