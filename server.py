from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qsl, quote, unquote
from pathlib import Path
from functools import cmp_to_key
from subprocess import Popen, PIPE
import os
import string
from os.path import expanduser, splitext
from base64 import standard_b64encode
import html
mpv_executable = 'mpv'
if os.name == 'nt':
    mpv_executable = 'mpv.com'

class MpvProcess(object):
    def __init__(self):
        self.mpv_process = None

class MpvServer(HTTPServer, MpvProcess): pass

class DirectoryViewer(object):

    def __init__(self, path):
        if path == 'WINROOT' and os.name == 'nt':
            self.as_html = self.list_windows_drives()
        else:
            self.path = Path(path)
            self.as_html = '<h1>{nav_links}</h1><hr><ul>{cont_links}</ul>'.format(
                    nav_links=self.generate_navigation_links(),
                    cont_links=self.generate_content_links(),
                )

    def list_windows_drives(self):
        drives = ['{}:\\'.format(letter) for letter in map(chr, range(65, 91))]
        drives = filter(os.path.isdir, drives)
        drive_link = '<li><a class="folder" href="/dir?path={link}"><i class="fa fa-folder"></i> {text}</a></li>'
        return '<ul>' + ''.join([drive_link.format(link=quote(d), text=d) for d in drives]) + '</ul>'

    def generate_navigation_links(self):
        navlinks = '<a class="navlink" href="/dir?path=WINROOT">(root)</a>|' if os.name == 'nt' else ''
        navlinks += os.sep.join(
            '<a class="navlink" href="/dir?path={0}">{1}</a>'.format(
                quote(str(d)), html.escape(d.parts[-1]))
                for d in list(reversed(self.path.parents)) + [self.path]
            )
        return navlinks

    def sort_compare(self, a, b):
        if a.is_dir():
            if b.is_dir():
                return (a.stat().st_mtime < b.stat().st_mtime) - .5
            else:
                return -1
        else:
            if b.is_dir():
                return 1
            else:
                return (str(a).lower() > str(b).lower()) - .5

    def generate_content_links(self):
        content = []
        content.append('<li><a class="file" href="/play?path={all}">'
                       '<i class="fa fa-asterisk"></i> (play all)</a></li>'.format(
                            all=quote(str(self.path / '*'))
                        )
                    )
        valid_items = []
        for i in self.path.iterdir():
            try:
                if i.exists(): valid_items.append(i)
            except Exception as e:
                print(e)
        for x in sorted(valid_items, key=cmp_to_key(self.sort_compare)):
            if x.parts[-1].startswith('.'): continue
            if x.is_file():
                function = 'play'
                vid_ext = ['avi', 'mp4', 'mkv', 'ogv', 'ogg', 'flv', 'm4v',
                           'mov', 'mpg', 'mpeg', 'wmv']
                if x.suffix[1:] in vid_ext:
                    cls = 'video'
                    fa = 'fa fa-file-video-o'
                else:
                    cls = 'file'
                    fa = ''
            elif x.is_dir():
                function = 'dir'
                cls = 'folder'
                fa = 'fa fa-folder'
            else:
                continue
            content.append(
                    '<li><a class="{cls}" href="/{function}?path={link}"><i class="{fa}"></i> {text}</a></li>'.format(
                        function=function, cls=cls, fa=fa, link=quote(str(x)), text=html.escape(x.parts[-1])
                    )
                )
        return ''.join(content)


class MpvRequestHandler(BaseHTTPRequestHandler):

    with open('template.html', 'r') as f: base = string.Template(f.read())
    with open('buttons.html', 'r') as f: buttons = f.read()
    with open('commands', 'r') as f:
        commands = dict()
        for c in f.read().splitlines():
            if not c: continue
            cname, command = c.split('=', 1)
            if command.startswith('file='):
                with open(command[len('file='):], 'r') as sf:
                    command = sf.read()
            commands[cname] = command
    with open('config', 'r') as f:
        config = ['--{}'.format(o) for o in f.read().splitlines()
                  if '=' in o and not o.startswith('#')]
    if os.path.isfile('login'):
        with open('login', 'rb') as f:
            login = standard_b64encode(f.read().strip())
            login = 'Basic {}'.format(login.decode())
    else: login = None

    controls = { 'content': buttons }
    controls = base.substitute(controls)

    def ask_auth(self):
        self.send_response(401)
        self.send_header('WWW-Authenticate', 'Basic realm="mpv-remote"')
        self.end_headers()

    def redirect(self, location):
        self.send_response(302)
        self.send_header('Location', location)
        self.end_headers()

    def respond_ok(self, data, content_type='text/html; charset=utf-8', age=0):
        self.send_response(200)
        self.send_header('Cache-Control', 'public, max-age={}'.format(age))
        self.send_header('Content-Type', content_type)
        self.end_headers()
        self.wfile.write(data)

    def respond_notfound(self, data=None):
        self.send_response(404)
        self.send_header('Content-Type', 'text/plain')
        self.end_headers()
        self.wfile.write(data or '404'.encode())

    def list_dir(self, path):
        try:
            listing = DirectoryViewer(path).as_html
        except Exception as e:
            return self.respond_notfound(str(e).encode())
        listing = self.base.substitute({'content': '<div class="listing">{}</div>'.format(listing)})
        self.respond_ok(listing.encode())

    def play_file(self, fpath):
        try:
            self.server.mpv_process.stdin.write(b'quit\n')
            self.server.mpv_process.kill()
        except Exception as e: print(e)
        cmd = [mpv_executable, '--input-terminal=no', '--input-file=/dev/stdin'] + self.config + ['--', fpath]
        self.server.mpv_process = Popen(cmd, stdin=PIPE)


    def serve_static(self):
        requested = unquote(self.path[len('/static/'):])
        if requested not in os.listdir('static'):
            self.respond_notfound('file not found'.encode())
        else:
            try:
                with open('static/'+requested, 'rb') as f:
                    ct = {'.css': 'text/css'}
                    self.respond_ok(
                        f.read(),
                        (ct.get(splitext(requested)[1]) or 'application/octet-stream'),
                        315360000
                        )
            except Exception as e:
                print(e)
                self.respond_notfound('error reading file'.encode())

    def control_mpv(self, command):
        try:
            mpv_stdin = self.server.mpv_process.stdin
            mpv_stdin.write((command + '\n').encode())
            mpv_stdin.flush()
        except Exception as e: print(e)
        h = 1
        if command == 'quit': h = 2
        self.respond_ok('<script>history.go(-{});</script>'.format(h).encode())

    def do_GET(self):

        if self.login:
            if self.headers.get('Authorization') == self.login:
                pass
            else:
                return self.ask_auth()

        url = urlparse(self.path)
        qs_list = dict(parse_qsl(url.query))

        if self.path.startswith('/static/'):
            self.serve_static()
        elif url.path == '/dir' and qs_list.get('path'):
            self.list_dir(qs_list['path'])
        elif url.path == '/play' and qs_list.get('path'):
            self.play_file(qs_list['path'])
            self.respond_ok(self.controls.encode())
        elif url.path == '/control' and qs_list.get('command') in self.commands:
            self.control_mpv(self.commands[qs_list['command']])
        elif self.path == '/':
            homedir = expanduser('~')
            self.redirect('/dir?path='+quote(homedir))
        else:
            self.respond_notfound()


if __name__ == '__main__':
    srv = MpvServer(('', 9876), MpvRequestHandler)
    srv.serve_forever()
