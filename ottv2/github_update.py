"""Download a public GitHub commit archive and use the existing READY gate."""

import argparse
from collections import OrderedDict
import os
import time
from http.client import HTTPException
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from time import monotonic
from threading import Lock
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .bot_store import import_zip
from .zip_session import MAX_ZIP_BYTES
from .update_gate import accepted_update

API_VERSION = '2026-03-10'
_ARCHIVES = OrderedDict()
_CACHE_LOCK = Lock()
_CACHE_BYTES = 8 * 1024 * 1024


class GitHubRateLimited(ValueError):
    def __init__(self, retry_at):
        self.retry_at = retry_at
        super().__init__('GitHub đang giới hạn tải; yêu cầu được giữ để thử lại.')


def _api_token():
    filename = os.environ.get('OTTV2_GITHUB_TOKEN_FILE')
    if not filename:
        return None
    path = Path(filename)
    if path.is_symlink() or path.is_junction() or not path.is_file() or path.stat().st_size > 1024:
        raise ValueError('Tệp xác thực GitHub chưa đúng.')
    if os.name != 'nt' and path.stat().st_mode & 0o077:
        raise ValueError('Tệp xác thực GitHub cần quyền 600.')
    token = path.read_text(encoding='ascii').strip()
    if not re.fullmatch(r'[A-Za-z0-9_]{20,512}', token):
        raise ValueError('Thông tin xác thực GitHub chưa đúng.')
    return token


def repository_name(url):
    if not isinstance(url, str) or len(url) > 250:
        raise ValueError('repository URL must be a string of at most 250 characters')
    parts = urlsplit(url.strip())
    if (parts.scheme != 'https' or parts.netloc.lower() != 'github.com'
            or parts.query or parts.fragment or parts.username or parts.password):
        raise ValueError('repository URL must be https://github.com/OWNER/REPO')
    names = parts.path.strip('/').split('/')
    if len(names) != 2:
        raise ValueError('repository URL must not contain branches or file paths')
    owner, repo = names
    if repo.endswith('.git'):
        repo = repo[:-4]
    if (not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}', owner)
            or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', repo) or repo in ('.', '..')):
        raise ValueError('invalid GitHub owner or repository name')
    return owner + '/' + repo


def allowed_download(url):
    parts = urlsplit(url)
    if (parts.scheme != 'https' or parts.hostname not in ('api.github.com', 'codeload.github.com')
            or parts.username or parts.password or parts.port not in (None, 443)):
        raise ValueError('download redirected outside approved HTTPS GitHub hosts')


class GitHubRedirects(HTTPRedirectHandler):
    def __init__(self):
        super().__init__()
        self.count = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        allowed_download(newurl)
        self.count += 1
        if self.count > 3:
            raise ValueError('GitHub download exceeded three redirects')
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None and urlsplit(newurl).hostname != 'api.github.com':
            redirected.remove_header('Authorization')
        return redirected


def fetch(url, limit, accept):
    allowed_download(url)
    request = Request(url, headers={'User-Agent': 'OTTv2-local', 'Accept': accept,
                                   'X-GitHub-Api-Version': API_VERSION, 'Accept-Encoding': 'identity'})
    if urlsplit(url).hostname == 'api.github.com':
        token = _api_token()
        if token:
            request.add_header('Authorization', 'Bearer ' + token)
    deadline = monotonic() + 20
    result = bytearray()
    try:
        with build_opener(GitHubRedirects()).open(request, timeout=5) as response:
            allowed_download(response.geturl())
            if response.status != 200:
                raise ValueError(f'GitHub response status {response.status}')
            length = response.headers.get('Content-Length')
            if length is not None and (not length.isdigit() or int(length) > limit):
                raise ValueError('GitHub response exceeds download limit')
            while True:
                if monotonic() >= deadline:
                    raise ValueError('GitHub download deadline exceeded')
                chunk = response.read1(min(65536, limit - len(result) + 1))
                if not chunk:
                    break
                result.extend(chunk)
                if len(result) > limit:
                    raise ValueError('GitHub response exceeds download limit')
            if length is not None and len(result) != int(length):
                raise ValueError('incomplete GitHub download')
    except HTTPError as error:
        if error.code == 429 or error.code == 403 and error.headers.get('X-RateLimit-Remaining') == '0':
            now = time.time()
            try:
                retry = (now + float(error.headers['Retry-After']) if error.headers.get('Retry-After')
                         else float(error.headers.get('X-RateLimit-Reset', now + 60)))
            except (ValueError, TypeError):
                retry = now + 60
            error.close()
            raise GitHubRateLimited(min(now + 3600, max(now + 5, retry))) from None
        raise ValueError(f'GitHub HTTP {error.code}; check repository visibility or API rate limit') from error
    except (URLError, TimeoutError, OSError, HTTPException) as error:
        raise ValueError(f'GitHub network error: {error}') from error
    return bytes(result)


def download_repository(url):
    name = repository_name(url)
    sha = fetch(f'https://api.github.com/repos/{name}/commits/HEAD', 256,
                'application/vnd.github.sha').decode('ascii').strip()
    if not re.fullmatch(r'[0-9a-f]{40}', sha):
        raise ValueError('GitHub returned an invalid commit SHA')
    # The archive is public and pinned: do not spend a second REST API request
    # or transmit the API credential to the archive host.
    key = (name, sha)
    with _CACHE_LOCK:
        data = _ARCHIVES.get(key)
        if data is not None:
            _ARCHIVES.move_to_end(key)
    if data is None:
        data = fetch(f'https://codeload.github.com/{name}/zip/{sha}', MAX_ZIP_BYTES,
                     'application/vnd.github+json')
        if len(data) <= _CACHE_BYTES:
            with _CACHE_LOCK:
                _ARCHIVES[key] = data
                _ARCHIVES.move_to_end(key)
                while len(_ARCHIVES) > 16 or sum(map(len, _ARCHIVES.values())) > _CACHE_BYTES:
                    _ARCHIVES.popitem(last=False)
    return data, {'type': 'github', 'repository': 'https://github.com/' + name, 'commit': sha}


def update_from_github(root, bot_id, repository, *, queue_database=None):
    repository_name(repository)  # Invalid URL is not an accepted request.
    if queue_database is not None:
        from .queue_admin import update_admission
        gate = accepted_update(root, bot_id, admission=update_admission(queue_database, root, bot_id))
    else:
        gate = accepted_update(root, bot_id)
    with gate:
        data, origin = download_repository(repository)
        with TemporaryDirectory(prefix='ottv2-download-') as tmp:
            path = Path(tmp) / 'source.zip'
            path.write_bytes(data)
            return import_zip(root, bot_id, path, origin=origin)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--store', type=Path, required=True)
    parser.add_argument('--bot-id', required=True)
    parser.add_argument('--repo', required=True)
    parser.add_argument('--queue', type=Path, help='apply queue operator locks to this update request')
    args = parser.parse_args()
    try:
        record = update_from_github(args.store, args.bot_id, args.repo, queue_database=args.queue)
        print(f"GITHUB BOT SAVED: {record['bot_id']} | commit={record['origin']['commit']} | version={record['version']}")
    except (ValueError, OSError, RuntimeError, UnicodeError) as error:
        parser.exit(1, f'GITHUB UPDATE ERROR: {error}\n')


if __name__ == '__main__':
    main()
