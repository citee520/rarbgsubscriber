import smtplib
import logging
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


LOG = logging.getLogger()


class HandlerManager(object):
    def __init__(self, conf):
        self._conf = conf
        self._handlers = []
        self._parse_conf()

    def _parse_conf(self):
        for name, value_dict in self._conf.iteritems():
            handler = None
            if name == "email":
                handler = EmailHandler(**value_dict)
            else:
                LOG.debug("Unknow handler %s", name)

            if handler:
                LOG.debug("append %s handler", type(handler))
                self._handlers.append(handler)


    def register(self, movie_info):
        for handler in self._handlers:
            handler.register(movie_info)

    def submit(self):
        for handler in self._handlers:
            handler.submit()


class Handler(object):
    def __init__(self):
        pass

    def init(self):
        pass

    def register(self, movie_info):
        pass

    def submit(self):
        pass


class EmailHandler(Handler):
    def __init__(self, host=None, port=None,
                 account=None, password=None, to=None):
        assert host
        assert port
        assert account
        assert password
        assert to

        self._host = host
        self._port = port
        self._account = account
        self._password = password
        self._to = to
        self._info = list()

    def register(self, movie_info):
        self._info.append(movie_info)

    def _info_to_html(self):
        html = "<html>"
        html += "<table>"
        html += """
            <tr>
                <th>Title</th>
                <th>Resoltion</th>
                <th>Format</th>
                <th>Size</th>
                <th>Video codec</th>
                <th>Audio codec</th>
                <th>Imdb</th>
                <th>Link</th>
            </tr>"""
        for info in self._info:
            html += """
                <tr>
                    <td>%s</td>
                    <td>%s</td>
                    <td>%s</td>
                    <td>%s</td>
                    <td>%s</td>
                    <td>%s</td>
                    <td>%s</td>
                    <td>%s</td>
                </tr>""" % (info.title, info.resolution, info.format,
                            info.size, info.video_codec, info.audio_codec,
                            info.imdb, info.href)
        html += "</table>"
        html += "</html>"
        return html

    def submit(self):
        today = date.today()
        if not self._info:
            LOG.info("%s without any updated torrent", today)
            return
        outer = MIMEMultipart()
        outer['Subject'] = "%s RARBG updated torrents" % today
        outer['From'] = 'RARBGspider'
        outer['To'] = ','.join(self._to)
        html = self._info_to_html()
        msg = MIMEText(html, 'html')
        outer.attach(msg)

        s = smtplib.SMTP(self._host, self._port)
        s.starttls()
        s.login(self._account, self._password)
        s.sendmail('rarbgspider@hotmail.com', self._to, outer.as_string())
        LOG.info("%s has %d new torrents, sending mail to %s",
                 today, len(self._info), self._to)
