import inspect
import logging

import sys

from oiccli import OIDCONF_PATTERN
from oiccli.exception import OicCliError
from oicmsg import oauth2
from oicmsg.exception import MissingParameter
from oicmsg.key_jar import KeyJar
from oicmsg.oauth2 import AuthorizationErrorResponse
from oicmsg.oauth2 import TokenErrorResponse

from oiccli.request import Request

__author__ = 'Roland Hedberg'

logger = logging.getLogger(__name__)

SUCCESSFUL = [200, 201, 202, 203, 204, 205, 206]

RESPONSE2ERROR = {
    "AuthorizationResponse": [AuthorizationErrorResponse, TokenErrorResponse],
    "AccessTokenResponse": [TokenErrorResponse]
}


def _post_x_parse_response(self, resp, session_info, state=''):
    try:
        _state = resp["state"]
    except (AttributeError, KeyError):
        _state = ""

    if not _state:
        _state = state

    _grant_db = session_info['grant_db']
    try:
        _grant_db[_state].update(resp)
    except KeyError:
        _grant_db[_state] = _grant_db.grant_class(resp=resp)
    except Exception as err:
        raise


class AuthorizationRequest(Request):
    msg_type = oauth2.AuthorizationRequest
    response_cls = oauth2.AuthorizationResponse
    error_msg = oauth2.AuthorizationErrorResponse
    endpoint_name = 'authorization_endpoint'
    synchronous = False
    request = 'authorization'

    def _parse_args(self, cli_info, **kwargs):
        ar_args = Request._parse_args(self, cli_info, **kwargs)

        if 'redirect_uri' not in ar_args:
            try:
                ar_args['redirect_uri'] = cli_info['redirect_uris'][0]
            except (KeyError, AttributeError):
                raise MissingParameter('redirect_uri')

        return ar_args

    def _post_parse_response(self, resp, session_info, state=''):
        _post_x_parse_response(self, resp, session_info, state='')

    def do_request_init(self, cli_info, state="", method="GET",
                        request_args=None, extra_args=None, http_args=None,
                        **kwargs):

        if state:
            try:
                request_args["state"] = state
            except TypeError:
                request_args = {"state": state}

        kwargs['authn_endpoint'] = 'authorization'
        _info = self.request_info(cli_info, method, request_args, extra_args,
                                  **kwargs)

        _info = self.update_http_args(http_args, _info)

        try:
            _info['algs'] = kwargs["algs"]
        except KeyError:
            _info['algs'] = {}

        return _info

    def construct(self, cli_info, request_args=None, extra_args=None, **kwargs):

        if request_args is not None:
            try:  # change default
                new = request_args["redirect_uri"]
                if new:
                    self.redirect_uris = [new]
            except KeyError:
                pass
        else:
            request_args = {}

        return Request.construct(self, cli_info, request_args, extra_args)


class AccessTokenRequest(Request):
    msg_type = oauth2.AccessTokenRequest
    response_cls = oauth2.AccessTokenResponse
    error_msg = oauth2.TokenErrorResponse
    endpoint_name = 'token_endpoint'
    synchronous = True
    request = 'accesstoken'

    def _post_parse_response(self, resp, session_info, state=''):
        _post_x_parse_response(self, resp, session_info, state='')

    def do_request_init(self, cli_info, scope="", state="", body_type="json",
                        method="POST", request_args=None,
                        extra_args=None, http_args=None,
                        authn_method="", **kwargs):

        kwargs['authn_endpoint'] = 'token'

        _info = self.request_info(
            cli_info, method=method, request_args=request_args,
            extra_args=extra_args, scope=scope, state=state,
            authn_method=authn_method, **kwargs)

        _info = self.update_http_args(http_args, _info)

        if self.events is not None:
            self.events.store('request_url', _info['url'])
            self.events.store('request_http_args', _info['http_args'])
            self.events.store('Request', _info['body'])

        logger.debug("<do_access_token> URL: {}, Body: {}".format(
            _info['uri'], _info['body']))
        logger.debug("<do_access_token> response_cls: {}".format(
            self.response_cls))

        return _info


class RefreshAccessTokenRequest(Request):
    msg_type = oauth2.RefreshAccessTokenRequest
    response_cls = oauth2.AccessTokenResponse
    error_msg = oauth2.TokenErrorResponse
    endpoint_name = 'token_endpoint'
    synchronous = True
    request = 'refresh_token'

    def do_request_init(self, cli_info, state="", method="POST",
                        request_args=None, extra_args=None, http_args=None,
                        authn_method="", **kwargs):
        _info = self.request_info(cli_info, method=method,
                                  request_args=request_args,
                                  extra_args=extra_args,
                                  token=kwargs['token'],
                                  authn_method=authn_method)

        _info = self.update_http_args(http_args, _info)
        return _info


class ProviderInfoDiscovery(Request):
    msg_type = oauth2.Message
    response_cls = oauth2.ASConfigurationResponse
    error_msg = oauth2.ErrorResponse
    synchronous = True
    request = 'provider_info'

    def request_info(self, cli_info, method="GET", request_args=None,
                     extra_args=None, lax=False, **kwargs):

        issuer = cli_info['issuer']

        if issuer.endswith("/"):
            _issuer = issuer[:-1]
        else:
            _issuer = issuer

        return {'uri': OIDCONF_PATTERN % _issuer}

    def _post_parse_response(self, resp, session_info, **kwargs):
        """
        Deal with Provider Config Response
        :param resp: The provider info response
        :param session_info: Information about the client/server session
        """
        issuer = session_info['issuer']

        if "issuer" in resp:
            _pcr_issuer = resp["issuer"]
            if resp["issuer"].endswith("/"):
                if issuer.endswith("/"):
                    _issuer = issuer
                else:
                    _issuer = issuer + "/"
            else:
                if issuer.endswith("/"):
                    _issuer = issuer[:-1]
                else:
                    _issuer = issuer

            try:
                session_info["allow_issuer_mismatch"]
            except KeyError:
                try:
                    assert _issuer == _pcr_issuer
                except AssertionError:
                    raise OicCliError(
                        "provider info issuer mismatch '%s' != '%s'" % (
                            _issuer, _pcr_issuer))

        else:  # No prior knowledge
            _pcr_issuer = issuer

        session_info['issuer'] = _pcr_issuer
        session_info['provider_info'] = resp

        for key, val in resp.items():
            if key.endswith("_endpoint"):
                for _srv in session_info['services']:
                    if _srv.endpoint_name == key:
                        _srv.endpoint = val

        try:
            kj = session_info['keyjar']
        except KeyError:
            kj = KeyJar()

        kj.load_keys(resp, _pcr_issuer)
        session_info['keyjar'] = kj


def factory(req_name, **kwargs):
    for name, obj in inspect.getmembers(sys.modules[__name__]):
        if inspect.isclass(obj) and issubclass(obj, Request):
            try:
                if obj.__name__ == req_name:
                    return obj(**kwargs)
            except AttributeError:
                pass
