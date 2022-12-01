import asyncio
import logging

import aiohttp
from aiohttp import web
from prometheus_client import Counter, Gauge, Histogram, CONTENT_TYPE_LATEST
import prometheus_client


check_http_probes_total = Counter('check_http_probes_total', 'number of probe requests', ['url'])
check_http_total = Counter('check_http_total', 'number of responses', ['url', 'error', 'code'])

logging.basicConfig(format='%(asctime)s %(levelname)s %(name)s %(message)s')

async def check_http(url):
    timeout = aiohttp.ClientTimeout(total=5)
    while True:
        async with aiohttp.ClientSession(raise_for_status=True, timeout=timeout) as session:

            for i in range(5):
                try:
                    check_http_probes_total.labels(url=url).inc()

                    async with session.get(url, allow_redirects=False) as resp:
                        check_http_total.labels(url=url, error='', code=resp.status).inc()

                except aiohttp.ClientSSLError as e:
                    check_http_total.labels(url=url, error='ssl', code=0).inc()
                    logging.exception(e)

                except aiohttp.ServerTimeoutError as e:
                    check_http_total.labels(url=url, error='timeout', code=0).inc()
                    logging.exception(e)

                except aiohttp.ClientConnectionError as e:
                    check_http_total.labels(url=url, error='connection', code=0).inc()
                    logging.exception(e)

                except aiohttp.ClientResponseError as e:
                    check_http_total.labels(url=url, error='response', code=e.status).inc()
                    logging.exception(e)

                except aiohttp.ClientError as e:
                    check_http_total.labels(url=url, error='other', code=0).inc()
                    logging.exception(e)

                await asyncio.sleep(1)

        print(prometheus_client.generate_latest())
        print('closed session')


async def metrics(request):
    resp = web.Response(body=prometheus_client.generate_latest())
    resp.content_type = CONTENT_TYPE_LATEST
    return resp


async def main():
    app = web.Application()
    app.add_routes([web.get('/metrics', metrics), ])

    web_runner = web.AppRunner(app)
    await web_runner.setup()
    web_site = web.TCPSite(web_runner, 'localhost', 8000)

    await asyncio.gather(
        check_http(url='https://google.com/'),
        web_site.start(),
    )


try:
    asyncio.run(main())
except KeyboardInterrupt:
    pass
