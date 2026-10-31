from html.parser import HTMLParser
import struct

from fastapi.testclient import TestClient

from app.main import app


class HeadElements(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.elements = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


def test_guest_pages_share_installable_dashboard_manifest():
    client = TestClient(app)
    client.cookies.clear()
    for route in ('/', '/alerts', '/lab', '/login'):
        response = client.get(route)
        assert response.status_code == 200
        elements = HeadElements(response.text).elements
        manifests = [attrs['href'] for tag, attrs in elements
                     if tag == 'link' and attrs.get('rel') == 'manifest']
        assert manifests == ['/static/alerts.webmanifest']
        assert ('meta', {'name': 'theme-color', 'content': '#087d68'}) in elements

    response = client.get(manifests[0])
    assert response.status_code == 200
    assert 'manifest+json' in response.headers['content-type']
    manifest = response.json()
    assert manifest['id'] == '/alerts'  # Preserve the existing installed app identity.
    assert manifest['name'] == '나의 투자 대시보드'
    assert manifest['start_url'] == manifest['scope'] == '/'
    assert manifest['display'] == 'standalone'
    assert manifest['theme_color'] == '#087d68'
    assert {(icon['sizes'], icon['purpose']) for icon in manifest['icons']} == {
        ('192x192', 'any'), ('512x512', 'any'), ('512x512', 'maskable'),
    }
    for icon in manifest['icons']:
        response = client.get(icon['src'])
        assert response.status_code == 200
        assert response.headers['content-type'] == icon['type'] == 'image/png'
        assert response.content[:8] == b'\x89PNG\r\n\x1a\n'
        width, height = struct.unpack('>II', response.content[16:24])
        assert icon['sizes'] == f'{width}x{height}'
