import asyncio
from datetime import datetime
import logging
import os
import re
import socket
import time
from types import SimpleNamespace

# to take effect, this envvar must be set before 'import prometheus_client'
os.environ.setdefault('PROMETHEUS_DISABLE_CREATED_SERIES', 'true')

import aiohttp
from aiohttp import web
from prometheus_client import Counter, Gauge, Histogram, CONTENT_TYPE_LATEST
import prometheus_client


check_http_probes_total = Counter('check_http_probes_total', 'number of probe requests', ['url'])
check_http_total = Counter('check_http_total', 'number of responses', ['url', 'error', 'code'])
check_httpd_latency_detail_seconds_total = Counter('check_httpd_latency_detail_seconds_total', 'number of seconds spent in each stage of request/response', ['url', 'stage'])
check_httpd_latency_seconds_total = Counter('check_httpd_latency_seconds_total', 'request/response latency', ['url'])

check_httpd_latency_cloudfront_total = Counter('check_httpd_latency_cloudfront_total', 'count of successful probes per CloudFront PoP', ['url', 'cf_pop'])
check_httpd_latency_cloudfront_seconds_total = Counter('check_httpd_latency_cloudfront_seconds_total', 'latency per CloudFront PoP', ['url', 'cf_pop'])
check_httpd_latency_cloudfront_detail_seconds_total = Counter('check_httpd_latency_cloudfront_detail_seconds_total', 'latency per CloudFront PoP', ['url', 'cf_pop', 'stage'])

RE_CF_SERVER_TIMING = re.compile(r'^([\w-]+).*dur=(\d+)')

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


async def on_request_start(session, ctx, params):
    ctx.request_start = time.perf_counter_ns()
    ctx.dns_resolvehost_start = None
    ctx.connection_create_start = None
    ctx.request_headers_sent = None
    ctx.sum_dns_resolvehost = 0
    ctx.sum_connection_create = 0

async def on_request_end_or_exception(session, ctx, params):
    ctx.response_end = time.perf_counter_ns()

    # collect latency from interrupted tasks (eg, exception during dns resolution)
    if ctx.dns_resolvehost_start:
        ctx.sum_dns_resolvehost += (ctx.response_end - ctx.dns_resolvehost_start)
    if ctx.connection_create_start:
        ctx.sum_connection_create += (ctx.response_end - ctx.connection_create_start)

    _dns = ctx.sum_dns_resolvehost
    _connection = (ctx.sum_connection_create - _dns)
    # if the request failed before headers were sent, take the time of exception
    _request_end = ctx.request_headers_sent or ctx.response_end
    _request = (_request_end - ctx.request_start) - (_dns + _connection)
    _response = ctx.response_end - _request_end
    _total = ctx.response_end - ctx.request_start

    # use 'trace_request_ctx' from request to pass the results
    ctx.trace_request_ctx.detail = dict(
        dns         = _dns / 10**9,
        connection  = _connection / 10**9,
        request     = _request / 10**9,
        response    = _response / 10**9,
    )
    ctx.trace_request_ctx.total = _total / 10**9

async def on_request_headers_sent(session, ctx, params):
    ctx.request_headers_sent = time.perf_counter_ns()

async def on_dns_resolvehost_start(session, ctx, params):
    ctx.dns_resolvehost_start = time.perf_counter_ns()

async def on_dns_resolvehost_end(session, ctx, params):
    ctx.sum_dns_resolvehost += (time.perf_counter_ns() - ctx.dns_resolvehost_start)
    ctx.dns_resolvehost_start = None

async def on_connection_create_start(session, ctx, params):
    ctx.connection_create_start = time.perf_counter_ns()

async def on_connection_create_end(session, ctx, params):
    ctx.sum_connection_create += (time.perf_counter_ns() - ctx.connection_create_start)
    ctx.connection_create_start = None

def latency_tracer():
    config = aiohttp.TraceConfig()
    config.on_request_start.append(on_request_start)
    config.on_dns_resolvehost_start.append(on_dns_resolvehost_start)
    config.on_dns_resolvehost_end.append(on_dns_resolvehost_end)
    config.on_connection_create_start.append(on_connection_create_start)
    config.on_connection_create_end.append(on_connection_create_end)
    config.on_request_headers_sent.append(on_request_headers_sent)
    config.on_request_end.append(on_request_end_or_exception)
    config.on_request_exception.append(on_request_end_or_exception)
    return config


async def check_http(url):
    timeout = aiohttp.ClientTimeout(total=5)

    logging.info('% starting monitoring loop', url)
    while True:
        async with aiohttp.ClientSession(timeout=timeout, trace_configs=[latency_tracer()]) as session:

            for i in range(60):
                latency = SimpleNamespace()
                tag_error = ''
                tag_code = 0
                tag_cf_pop = ''
                tag_cf_detail = {}
                query_start_time = datetime.utcnow().isoformat()
                try:
                    async with session.get(url, allow_redirects=False, trace_request_ctx=latency) as resp:
                        body = await resp.text()
                        try:
                            resp.raise_for_status()
                            tag_code = resp.status

                            if 'x-amz-cf-pop' in resp.headers:
                                tag_cf_pop = resp.headers['x-amz-cf-pop'][:3]
                            if 'server-timing' in resp.headers:
                                for element in resp.headers['server-timing'].split(','):
                                    m = RE_CF_SERVER_TIMING.match(element)
                                    if m:
                                        tag_cf_detail[m.group(1)] = int(m.group(2)) / 1000

                        except aiohttp.ClientResponseError as e:
                            tag_error = 'response'
                            tag_code = e.status
                            logging.error('%s %s headers %s body %s start=%s', url, e, e.headers, body, query_start_time)

                # Hierarchy of exceptions
                # https://docs.aiohttp.org/en/stable/client_reference.html#hierarchy-of-exceptions

                except aiohttp.ClientSSLError as e:
                    tag_error = 'ssl'
                    logging.error('%s %s start=%s', url, e, query_start_time)

                except (aiohttp.ServerTimeoutError, asyncio.TimeoutError) as e:
                    tag_error = 'timeout'
                    logging.error('%s timeout start=%s', url, query_start_time)

                except aiohttp.ClientConnectionError as e:
                    if isinstance(e.__context__, ConnectionRefusedError):
                        tag_error = 'connection_refused'
                        logging.error('%s connection refused %s start=%s', url, e, query_start_time)
                    if isinstance(e.__context__, socket.gaierror):
                        (error_code, error_string) = e.__context__.args
                        if error_code == socket.EAI_NONAME:
                            tag_error = 'dns_notfound'
                        elif error_code == socket.EAI_AGAIN:
                            tag_error = 'dns_tryagain'
                        else:
                            tag_error = 'dns'
                        logging.error('%s dns error %s (%d) %s start=%s', url, e, error_code, error_string, query_start_time)
                    else:
                        tag_error = 'connection'
                        logging.error('%s connection error %s context %s start=%s', url, e, repr(e.__context__), query_start_time)

                except aiohttp.ClientError as e:
                    tag_error = 'other'
                    logging.error('%s client error %s context %s start=%s', url, e, repr(e.__context__), query_start_time)

                check_http_probes_total.labels(url=url).inc()
                check_http_total.labels(url=url, error=tag_error, code=tag_code).inc()
                for k,v in latency.detail.items():
                    check_httpd_latency_detail_seconds_total.labels(url=url, stage=k).inc(v)
                check_httpd_latency_seconds_total.labels(url=url).inc(latency.total)

                if tag_cf_pop:
                    check_httpd_latency_cloudfront_total.labels(url=url, cf_pop=tag_cf_pop).inc()
                    check_httpd_latency_cloudfront_seconds_total.labels(url=url, cf_pop=tag_cf_pop).inc(latency.total)
                    for k,v in tag_cf_detail.items():
                        check_httpd_latency_cloudfront_detail_seconds_total.labels(url=url, cf_pop=tag_cf_pop, stage=k).inc(v)

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
