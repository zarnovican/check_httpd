import asyncio

import aiohttp
from aiohttp import web


routes = web.RouteTableDef()


@routes.get('/')
@routes.get('/ok')
async def ok(request):
    return web.Response(text='ok')


@routes.get('/error')
async def error(request):
    some_dummy_multiline_html = """<html>
<h1>Error

Something really BAD happened
</html>"""
    return web.Response(status=503, text=some_dummy_multiline_html, content_type='text/html')


@routes.get('/error_json')
async def error(request):
    return web.Response(status=503, text='{"message":"error message"}', content_type='application/json')


@routes.get('/slow1s')
async def ok(request):
    await asyncio.sleep(1)
    return web.Response(text='slow 1s')


@routes.get('/slow10s')
async def ok(request):
    await asyncio.sleep(10)
    return web.Response(text='slow 10s')


app = web.Application()
app.add_routes(routes)

web.run_app(app, port=8001)
