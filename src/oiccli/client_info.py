import hashlib
import os

from jwkest import b64e, as_bytes
from oiccli import CC_METHOD
from oiccli import DEF_SIGN_ALG
from oiccli import unreserved
from oiccli.exception import Unsupported
from oiccli.state import State
from oicmsg.key_jar import KeyJar

ATTRMAP = {
    "userinfo": {
        "sign": "userinfo_signed_response_alg",
        "alg": "userinfo_encrypted_response_alg",
        "enc": "userinfo_encrypted_response_enc"},
    "id_token": {
        "sign": "id_token_signed_response_alg",
        "alg": "id_token_encrypted_response_alg",
        "enc": "id_token_encrypted_response_enc"},
    "request": {
        "sign": "request_object_signing_alg",
        "alg": "request_object_encryption_alg",
        "enc": "request_object_encryption_enc"}
}


class ClientInfo(object):
    def __init__(self, keyjar=None, config=None, events=None,
                 db=None, db_name='', strict_on_preferences=False, **kwargs):
        self.keyjar = keyjar or KeyJar()
        self.state_db = State('', db=db, db_name=db_name)
        self.events = events
        self.strict_on_preferences = strict_on_preferences
        self.provider_info = {}
        self.registration_response = {}
        self.kid = {"sig": {}, "enc": {}}

        self.config = config or {}
        # Below so my IDE won't complain
        self.base_url = ''
        self.requests_dir = ''
        self.allow = {}
        self.behaviour = {}
        self.client_prefs = {}
        self._c_id = ''
        self._c_secret = ''
        self.issuer = ''

        for key, val in kwargs.items():
            setattr(self, key, val)

        for attr in ['client_id', 'issuer', 'client_secret', 'base_url',
                     'requests_dir']:
            try:
                setattr(self, attr, config[attr])
            except:
                setattr(self, attr, '')
            else:
                if attr == 'client_id':
                    self.state_db.client_id = config[attr]

        for attr in ['allow', 'client_prefs', 'behaviour']:
            try:
                setattr(self, attr, config[attr])
            except:
                setattr(self, attr, {})

        if self.requests_dir:
            if not os.path.isdir(self.requests_dir):
                os.makedirs(self.requests_dir)

        try:
            self.redirect_uris = config['redirect_uris']
        except:
            self.redirect_uris = [None]

    def get_client_secret(self):
        return self._c_secret

    def set_client_secret(self, val):
        if not val:
            self._c_secret = ""
        else:
            self._c_secret = val
            # client uses it for signing
            # Server might also use it for signing which means the
            # client uses it for verifying server signatures
            if self.keyjar is None:
                self.keyjar = KeyJar()
            self.keyjar.add_symmetric("", str(val))

    client_secret = property(get_client_secret, set_client_secret)

    def get_client_id(self):
        return self._c_id

    def set_client_id(self, client_id):
        self._c_id = client_id
        self.state_db.client_id = client_id

    client_id = property(get_client_id, set_client_id)

    def __setitem__(self, key, value):
        setattr(self, key, value)

    def filename_from_webname(self, webname):
        assert webname.startswith(self.base_url)
        _name = webname[len(self.base_url):]
        if _name.startswith('/'):
            return _name[1:]
        else:
            return _name

    def sign_enc_algs(self, typ):
        """

        :param typ: 'id_token', 'userinfo' or 'request_object'
        :return:
        """
        resp = {}
        for key, val in ATTRMAP[typ].items():
            try:
                resp[key] = self.registration_response[val]
            except (TypeError, KeyError):
                if key == "sign":
                    try:
                        resp[key] = DEF_SIGN_ALG[typ]
                    except KeyError:
                        pass
        return resp

    def verify_alg_support(self, alg, usage, typ):
        """
        Verifies that the algorithm to be used are supported by the other side.

        :param alg: The algorithm specification
        :param usage: In which context the 'alg' will be used.
            The following values are supported:
            - userinfo
            - id_token
            - request_object
            - token_endpoint_auth
        :param typ:
            - signing_alg
            - encryption_alg
            - encryption_enc
        :return: True or False
        """

        supported = self.provider_info[
            "{}_{}_values_supported".format(usage, typ)]

        if alg in supported:
            return True
        else:
            return False

    def generate_request_uris(self, request_dir):
        """
        Need to generate a path that is unique for the OP/RP combo

        :return: A list of one unique URL
        """
        m = hashlib.sha256()
        try:
            m.update(as_bytes(self.provider_info['issuer']))
        except KeyError:
            m.update(as_bytes(self.issuer))
        m.update(as_bytes(self.base_url))
        return ['{}{}/{}'.format(self.base_url, request_dir, m.hexdigest())]


def add_code_challenge(client_info):
    """
    PKCE RFC 7636 support

    :return:
    """
    try:
        cv_len = client_info.config['code_challenge']['length']
    except KeyError:
        cv_len = 64  # Use default

    code_verifier = unreserved(cv_len)
    _cv = code_verifier.encode()

    try:
        _method = client_info.config['code_challenge']['method']
    except KeyError:
        _method = 'S256'

    try:
        _h = CC_METHOD[_method](_cv).hexdigest()
        code_challenge = b64e(_h.encode()).decode()
    except KeyError:
        raise Unsupported(
            'PKCE Transformation method:{}'.format(_method))

    # TODO store code_verifier

    return {"code_challenge": code_challenge,
            "code_challenge_method": _method}, code_verifier