from .json_import import simplejson
from six.moves.urllib.parse import urlencode
from httplib2 import Http
from hashlib import sha256
import mimetypes
import six
import hmac


class OAuth2AuthExchangeError(Exception):
    def __init__(self, description):
        self.description = description

    def __str__(self):
        return self.description


class OAuth2API(object):
    host = None
    base_path = None
    authorize_url = None
    access_token_url = None
    redirect_uri = None
    # some providers use "oauth_token"
    access_token_field = "access_token"
    protocol = "https"
    # override with 'Instagram', etc
    api_name = "Generic API"

    def __init__(self, client_id=None, client_secret=None, client_ips=None, access_token=None, redirect_uri=None, timeout=None):
        self.client_id = client_id
        self.client_secret = client_secret
        self.client_ips = client_ips
        self.access_token = access_token
        self.redirect_uri = redirect_uri
        self.timeout = timeout

    def get_authorize_url(self, scope=None):
        req = OAuth2AuthExchangeRequest(self)
        return req.get_authorize_url(scope=scope)

    def get_authorize_login_url(self, scope=None):
        """ scope should be a tuple or list of requested scope access levels """
        req = OAuth2AuthExchangeRequest(self)
        return req.get_authorize_login_url(scope=scope)

    def exchange_code_for_access_token(self, code):
        req = OAuth2AuthExchangeRequest(self)
        return req.exchange_for_access_token(code=code)

    def exchange_user_id_for_access_token(self, user_id):
        req = OAuth2AuthExchangeRequest(self)
        return req.exchange_for_access_token(user_id=user_id)

    def exchange_xauth_login_for_access_token(self, username, password, scope=None):
        """ scope should be a tuple or list of requested scope access levels """
        req = OAuth2AuthExchangeRequest(self)
        return req.exchange_for_access_token(username=username, password=password, scope=scope)


class OAuth2AuthExchangeRequest(object):
    def __init__(self, api):
        self.api = api

    def _url_for_authorize(self, scope=None):
        client_params = {
            "client_id": self.api.client_id,
            "response_type": "code",
            "redirect_uri": self.api.redirect_uri
        }

        if scope:
            client_params.update(scope=' '.join(scope))

        url_params = urlencode(client_params)
        return "{url}?{params}".format(url=self.api.authorize_url, params=url_params)

    def _data_for_exchange(self, code=None, username=None, password=None, scope=None, user_id=None):
        client_params = {
            "client_id": self.api.client_id,
            "client_secret": self.api.client_secret,
            "redirect_uri": self.api.redirect_uri,
            "grant_type": "authorization_code"
        }

        if code:
            client_params.update(code=code)
        elif username and password:
            client_params.update(username=username,
                                 password=password,
                                 grant_type="password")
            if scope:
                client_params.update(scope=' '.join(scope))

        elif user_id:
            client_params.update(user_id=user_id)

        return urlencode(client_params)

    def get_authorize_url(self, scope=None):
        return self._url_for_authorize(scope=scope)

    def get_authorize_login_url(self, scope=None):
        http_object = Http(timeout=self.api.timeout, disable_ssl_certificate_validation=True)

        url = self._url_for_authorize(scope=scope)
        response, content = http_object.request(url)
        if response['status'] != '200':
            raise OAuth2AuthExchangeError("The server returned a non-200 response for URL {url}".format(url))

        redirected_to = response['Content-Location']
        return redirected_to

    def exchange_for_access_token(self, code=None, username=None, password=None, scope=None, user_id=None):
        data = self._data_for_exchange(code, username, password, scope=scope, user_id=user_id)
        http_object = Http(timeout=self.api.timeout, disable_ssl_certificate_validation=True)
        url = self.api.access_token_url

        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        response, content = http_object.request(url, method="POST", body=data, headers=headers)
        parsed_content = simplejson.loads(content.decode())

        if int(response['status']) != 200:
            raise OAuth2AuthExchangeError(parsed_content.get("error_message", ""))

        return parsed_content['access_token'], parsed_content['user']


class OAuth2Request(object):
    def __init__(self, api):
        self.api = api

    def _generate_sig(self, endpoint, params, secret):
        # handle unicode when signing, urlencode can't handle otherwise.
        def enc_if_str(p):
            return p.encode('utf-8') if isinstance(p, six.text_type) else p

        path = ''.join('|{key}={val}'.format(key=key, val=enc_if_str(params[key])) for key in sorted(params.keys()))
        sig = '{endpoint}{path}'.format(endpoint=endpoint, path=path)
        return hmac.new(secret.encode(), sig.encode(), sha256).hexdigest()

    def url_for_get(self, path, parameters):
        return self._full_url_with_params(path, parameters)

    def get_request(self, path, **kwargs):
        return self.make_request(self.prepare_request("GET", path, kwargs))

    def post_request(self, path, **kwargs):
        return self.make_request(self.prepare_request("POST", path, kwargs))

    # TODO - make use of six.moves.urllib.parse.urlparse for all this string munging
    def _full_url(self, path, include_secret=False, include_signed_request=True):
        signed_request = self._signed_request(path, {}, include_signed_request, include_secret)
        return "{protocol}://{host}{basepath}{path}{query}{signed}".format(
            protocol=self.api.protocol, host=self.api.host, basepath=self.api.base_path, path=path,
            query=self._auth_query(include_secret), signed=signed_request)

    def _full_url_with_params(self, path, params, include_secret=False, include_signed_request=True):
        signed_request = self._signed_request(path, params, include_signed_request, include_secret)
        return "{url}{query}{signed}".format(
            url=self._full_url(path, include_secret), query=self._full_query_with_params(params), signed=signed_request)

    def _full_query_with_params(self, params):
        if not params:
            return ""
        return "&{params}".format(params=urlencode(params))

    def _auth_query(self, include_secret=False):
        if self.api.access_token:
            return "?{field}={token}".format(field=self.api.access_token_field, token=self.api.access_token)
        elif self.api.client_id:
            base = "?client_id={client_id}".format(client_id=self.api.client_id)
            if include_secret:
                base += "&client_secret={client_secret}".format(client_secret=self.api.client_secret)
            return base

    def _signed_request(self, path, params, include_signed_request, include_secret):
        if include_signed_request and self.api.client_secret is not None:
            if self.api.access_token:
                params['access_token'] = self.api.access_token
            elif self.api.client_id:
                params['client_id'] = self.api.client_id

            if include_secret and self.api.client_secret:
                params['client_secret'] = self.api.client_secret

            return "&sig={signed}".format(signed=self._generate_sig(path, params, self.api.client_secret))
        else:
            return ''

    def _post_body(self, params):
        return urlencode(params)

    def _encode_multipart(self, params, files):
        boundary = "MuL7Ip4rt80uND4rYF0o"

        def get_content_type(file_name):
            return mimetypes.guess_type(file_name)[0] or "application/octet-stream"

        def encode_field(field_name):
            return ("--{boundary}".format(boundary=boundary),
                    'Content-Disposition: form-data; name="{field_name}"'.format(field_name=field_name),
                    "", str(params[field_name]))

        def encode_file(field_name):
            file_name, file_handle = files[field_name]
            return ("--{boundary}".format(boundary=boundary),
                    'Content-Disposition: form-data; name="{field_name}"; filename="{file_name}"'.format(
                        field_name=field_name, file_name=file_name),
                    "Content-Type: {content_type}".format(content_type=get_content_type(file_name)),
                    "", file_handle.read())

        lines = []
        for field in params:
            lines.extend(encode_field(field))
        for field in files:
            lines.extend(encode_file(field))

        lines.extend(("--{boundary}--".format(boundary=boundary), ""))
        body = "\r\n".join(lines)

        headers = {"Content-Type": "multipart/form-data; boundary={boundary}".format(boundary=boundary),
                   "Content-Length": str(len(body))}

        return body, headers

    def prepare_and_make_request(self, method, path, params, include_secret=False):
        url, method, body, headers = self.prepare_request(method, path, params, include_secret)
        return self.make_request(url, method, body, headers)

    def prepare_request(self, method, path, params, include_secret=False):
        url = body = None
        headers = {}

        if not params.get('files'):
            if method == "POST":
                body = self._post_body(params)
                headers = {'Content-Type': 'application/x-www-form-urlencoded'}

            url = self._full_url_with_params(path, params, include_secret)
        else:
            body, headers = self._encode_multipart(params, params['files'])
            url = self._full_url(path)

        return url, method, body, headers

    def make_request(self, url, method="GET", body=None, headers=None):
        headers = headers or {}
        if 'User-Agent' not in headers:
            headers.update({"User-Agent": "{api_name} Python Client".format(api_name=self.api.api_name)})

        # https://github.com/jcgregorio/httplib2/issues/173
        # bug in httplib2 w/ Python 3 and disable_ssl_certificate_validation=True
        if six.PY3:
            http_obj = Http(timeout=self.api.timeout)
        else:
            http_obj = Http(timeout=self.api.timeout, disable_ssl_certificate_validation=True)

        return http_obj.request(url, method, body=body, headers=headers)
