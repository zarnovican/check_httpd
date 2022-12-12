import asyncio
from datetime import datetime
import logging
import os
import socket

import aiohttp
from aiohttp import web
from prometheus_client import Counter, Gauge, Histogram, CONTENT_TYPE_LATEST
import prometheus_client


check_http_probes_total = Counter('check_http_probes_total', 'number of probe requests', ['url'])
check_http_total = Counter('check_http_total', 'number of responses', ['url', 'error', 'code'])


class Config():

    def __init__(self, env):
        self.urls_raw   = env.get('URLS', '')
        self.log_level  = env.get('LOG_LEVEL', 'info').upper()

        self.urls = [ url.strip() for url in self.urls_raw.split(',') ]
        self.urls = [ url for url in self.urls if url != '' ]

        logging.basicConfig(format='%(asctime)s %(levelname)s %(name)s %(message)s')
        logging.getLogger().setLevel(logging.getLevelName(self.log_level))

        logging.info('Starting "check_httpd"')
        logging.info('  URLS=%s', self.urls_raw)


async def check_http(url):
    timeout = aiohttp.ClientTimeout(total=5)

    logging.info('% starting monitoring loop', url)
    while True:
        async with aiohttp.ClientSession(timeout=timeout) as session:

            for i in range(60):
                check_http_probes_total.labels(url=url).inc()

                query_start_time = datetime.utcnow().isoformat()
                try:
                    async with session.get(url, allow_redirects=False) as resp:
                        body = await resp.text()
                        try:
                            resp.raise_for_status()

                            check_http_total.labels(url=url, error='', code=resp.status).inc()

                        except aiohttp.ClientResponseError as e:
                            check_http_total.labels(url=url, error='response', code=e.status).inc()
                            logging.error('%s %s headers %s body %s start=%s', url, e, e.headers, body, query_start_time)

                # Hierarchy of exceptions
                # https://docs.aiohttp.org/en/stable/client_reference.html#hierarchy-of-exceptions

                except aiohttp.ClientSSLError as e:
                    check_http_total.labels(url=url, error='ssl', code=0).inc()
                    logging.error('%s %s start=%s', url, e, query_start_time)

                except (aiohttp.ServerTimeoutError, asyncio.TimeoutError) as e:
                    check_http_total.labels(url=url, error='timeout', code=0).inc()
                    logging.error('%s timeout start=%s', url, query_start_time)

                except aiohttp.ClientConnectionError as e:
                    if isinstance(e.__context__, ConnectionRefusedError):
                        check_http_total.labels(url=url, error='connection_refused', code=0).inc()
                        logging.error('%s connection refused %s start=%s', url, e, query_start_time)
                    if isinstance(e.__context__, socket.gaierror):
                        (error_code, error_string) = e.__context__.args
                        if error_code == socket.EAI_NONAME:
                            check_http_total.labels(url=url, error='dns_notfound', code=0).inc()
                        elif error_code == socket.EAI_AGAIN:
                            check_http_total.labels(url=url, error='dns_tryagain', code=0).inc()
                        else:
                            check_http_total.labels(url=url, error='dns', code=0).inc()
                        logging.error('%s dns error %s (%d) %s start=%s', url, e, error_code, error_string, query_start_time)
                    else:
                        check_http_total.labels(url=url, error='connection', code=0).inc()
                        logging.error('%s connection error %s context %s start=%s', url, e, repr(e.__context__), query_start_time)

                except aiohttp.ClientError as e:
                    check_http_total.labels(url=url, error='other', code=0).inc()
                    logging.error('%s client error %s context %s start=%s', url, e, repr(e.__context__), query_start_time)

                await asyncio.sleep(1)


async def metrics(request):
    resp = web.Response(body=prometheus_client.generate_latest())
    resp.content_type = CONTENT_TYPE_LATEST
    return resp


async def main():
    config = Config(os.environ)

    if len(config.urls) == 0:
        logging.warning('The list of urls to monitor is empty (env var URLS)')
        return

    app = web.Application()
    app.add_routes([web.get('/metrics', metrics), ])

    web_runner = web.AppRunner(app)
    await web_runner.setup()
    web_site = web.TCPSite(web_runner, '0.0.0.0', 8000)

    # one AIO task for web server exposing /metrics
    tasks = [ web_site.start() ]
    for url in config.urls:
        # one AIO task per monitored url
        tasks.append(check_http(url=url))

    await asyncio.gather(*tasks)


try:
    asyncio.run(main())
except KeyboardInterrupt:
    pass
