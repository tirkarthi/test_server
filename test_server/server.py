from threading import Thread
from tornado.ioloop import IOLoop
import tornado.web
import time
import collections
import tornado.gen
from tornado.httpserver import HTTPServer
from six.moves.urllib.parse import urljoin

__all__ = ('TestServer',)


class TestServerRuntimeError(Exception):
    pass


class TestServer(object):
    request = {}
    response = {}
    response_once = {'headers': []}
    sleep = {}
    timeout_iterator = None
    methods = ('get', 'post', 'head', 'options', 'put', 'delete',
               'patch', 'trace', 'connect')

    def __init__(self, port=9876, address='127.0.0.1', extra_ports=None):
        self.port = port
        self.address = address
        self.extra_ports = list(extra_ports or [])
        self.reset()
        self._handler = None
        self._thread = None

    def get_param(self, key, method='get', clear_once=True):
        method_key = '%s.%s' % (method, key)
        if method_key in self.response_once:
            value = self.response_once[method_key]
            if clear_once:
                del self.response_once[method_key]
            return value
        elif key in self.response_once:
            value = self.response_once[key]
            if clear_once:
                del self.response_once[key]
            return value
        elif method_key in self.response:
            return self.response[method_key]
        elif key in self.response:
            return self.response[key]
        else:
            raise TestServerRuntimeError('Parameter %s does not exists in '
                                         'server response data' % key)

    def reset(self):
        self.request.update({
            'args': {},
            'headers': {},
            'cookies': None,
            'path': None,
            'method': None,
            'charset': 'utf-8',
            'data': None,
        })
        self.response = {
            'code': 200,
            'data': '',
            'headers': [],
            'cookies': [],
            'callback': None,
            'sleep': None,
        }

        self.response_once = {}

    def get_handler(self):
        "Build tornado request handler that is used in HTTP server"
        SERVER = self

        class MainHandler(tornado.web.RequestHandler):
            def decode_argument(self, value, **kwargs):
                # pylint: disable=unused-argument
                return value.decode(SERVER.request['charset'])

            @tornado.web.asynchronous
            @tornado.gen.engine
            def method_handler(self):
                method = self.request.method.lower()

                sleep = SERVER.get_param('sleep', method)
                if sleep:
                    yield tornado.gen.Task(IOLoop.instance().add_timeout,
                                           time.time() + sleep)
                SERVER.request['args'] = {}
                for key in self.request.arguments.keys():
                    SERVER.request['args'][key] = self.get_argument(key)
                SERVER.request['headers'] = self.request.headers
                SERVER.request['path'] = self.request.path
                SERVER.request['method'] = self.request.method
                SERVER.request['cookies'] = self.request.cookies
                charset = SERVER.request['charset']
                SERVER.request['data'] = self.request.body

                callback_name = '%s_callback' % method
                if SERVER.response.get(callback_name) is not None:
                    SERVER.response[callback_name](self)
                else:
                    headers_sent = set()

                    self.set_status(SERVER.get_param('code', method))
                    for key, val in SERVER.get_param('cookies', method):
                        # Set-Cookie: name=newvalue; expires=date;
                        # path=/; domain=.example.org.
                        self.add_header('Set-Cookie', '%s=%s' % (key, val))

                    for key, value in SERVER.get_param('headers', method):
                        self.set_header(key, value)
                        headers_sent.add(key)

                    self.set_header('Listen-Port',
                                    str(self.application.listen_port))

                    if 'Content-Type' not in headers_sent:
                        charset = 'utf-8'
                        self.set_header('Content-Type',
                                        'text/html; charset=%s' % charset)
                        headers_sent.add('Content-Type')

                    data = SERVER.get_param('data', method)
                    if isinstance(data, collections.Callable):
                        self.write(data())
                    else:
                        self.write(data)

                    if SERVER.timeout_iterator:
                        yield tornado.gen.Task(IOLoop.instance().add_timeout,
                                               time.time() +
                                               next(SERVER.timeout_iterator))
                    self.finish()

            get = method_handler
            post = method_handler
            put = method_handler
            patch = method_handler
            delete = method_handler

        if not self._handler:
            self._handler = MainHandler
        return self._handler

    def _build_web_app(self):
        """Build tornado web application that is served by
        HTTP server"""
        return tornado.web.Application([
            (r"^.*", self.get_handler()),
        ])

    def main_loop_function(self):
        """This is function that is executed in separate thread:
         * start HTTP server
         * start tornado loop"""
        ports = [self.port] + self.extra_ports
        servers = []
        for port in ports:
            app = self._build_web_app()
            app.listen_port = port
            server = HTTPServer(app)
            server.listen(port, self.address)
            print('Listening on port %d' % port)
            servers.append(server)

        tornado.ioloop.IOLoop.instance().start()

        # manually close sockets
        # to be able to create other HTTP servers
        # on same sockets
        for server in servers:
            # pylint: disable=protected-access
            for socket in server._sockets.values():
                socket.close()

    def start(self):
        """Create new thread with tornado loop and start there
        HTTP server."""

        self._thread = Thread(target=self.main_loop_function)
        self._thread.start()
        time.sleep(0.1)

    def stop(self):
        "Stop tornado loop and wait for thread finished it work"
        tornado.ioloop.IOLoop.instance().stop()
        self._thread.join()

    def get_url(self, extra='', port=None):
        "Build URL that is served by HTTP server"
        if port is None:
            port = self.port
        return urljoin('http://%s:%d/' % (self.address, port), extra)
