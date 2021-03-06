from nose.tools import set_trace
import requests
import urlparse
from flask_babel import lazy_gettext as _
from problem_detail import ProblemDetail as pd

INTEGRATION_ERROR = pd(
      "http://librarysimplified.org/terms/problem/remote-integration-failed",
      502,
      _("Third-party service failed."),
      _("A third-party service has failed."),
)

class RemoteIntegrationException(Exception):

    """An exception that happens when communicating with a third-party
    service.
    """
    title = _("Failure contacting external service")
    detail = _("The server tried to access %(service)s but the third-party service experienced an error.")
    internal_message = "Error accessing %s: %s"

    def __init__(self, url_or_service, message, debug_message=None):
        """Indicate that a remote integration has failed.
        
        `param url_or_service` The name of the service that failed
           (e.g. "Overdrive"), or the specific URL that had the problem.
        """
        super(RemoteIntegrationException, self).__init__(message)
        if (url_or_service and
            any(url_or_service.startswith(x) for x in ('http:', 'https:'))):
            self.url = url_or_service
            self.service = urlparse.urlparse(url_or_service).netloc
        else:
            self.url = self.service = url_or_service
        self.debug_message = debug_message

    def __str__(self):
        return self.internal_message % (self.url, self.message)

    def document_detail(self, debug=True):
        if debug:
            return _(unicode(self.detail), service=self.url)
        return _(unicode(self.detail), service=self.service)

    def document_debug_message(self, debug=True):
        if debug:
            return _(unicode(self.detail), service=self.url)
        return None

    def as_problem_detail_document(self, debug):
        return INTEGRATION_ERROR.detailed(
            detail=self.document_detail(debug), title=self.title, 
            debug_message=self.document_debug_message(debug)
        )

class BadResponseException(RemoteIntegrationException):
    """The request seemingly went okay, but we got a bad response."""
    title = _("Bad response")
    detail = _("The server made a request to %(service)s, and got an unexpected or invalid response.")
    internal_message = "Bad response from %s: %s"

    BAD_STATUS_CODE_MESSAGE = "Got status code %s from external server, cannot continue."

    def __init__(self, url_or_service, message, debug_message=None, status_code=None):
        """Indicate that a remote integration has failed.
        
        `param url_or_service` The name of the service that failed
           (e.g. "Overdrive"), or the specific URL that had the problem.
        """
        super(BadResponseException, self).__init__(url_or_service, message, debug_message)
        # to be set to 500, etc.
        self.status_code = status_code

    def document_debug_message(self, debug=True):
        if debug:
            msg = self.message
            if self.debug_message:
                msg += "\n\n" + self.debug_message
            return msg
        return None

    @classmethod
    def from_response(cls, url, message, response):
        """Helper method to turn a `requests` Response object into
        a BadResponseException.
        """
        if isinstance(response, tuple):
            # The response has been unrolled into a (status_code,
            # headers, body) 3-tuple.
            status_code, headers, content = response
        else:
            status_code = response.status_code
            content = response.content
        return BadResponseException(
            url, message, 
            status_code=status_code, 
            debug_message="Status code: %s\nContent: %s" % (
                status_code,
                content,
            )
        )

    @classmethod
    def bad_status_code(cls, url, response):
        """The response is bad because the status code is wrong."""
        message = cls.BAD_STATUS_CODE_MESSAGE % response.status_code
        return cls.from_response(
            url,
            message,
            response,
        )


class RequestNetworkException(RemoteIntegrationException,
                              requests.exceptions.RequestException):
    """An exception from the requests module that can be represented as
    a problem detail document.
    """
    title = _("Network failure contacting third-party service")
    detail = _("The server experienced a network error while contacting %(service)s.")
    internal_message = "Network error contacting %s: %s"


class RequestTimedOut(RequestNetworkException, requests.exceptions.Timeout):
    """A timeout exception that can be represented as a problem
    detail document.
    """

    title = _("Timeout")
    detail = _("The server made a request to %(service)s, and that request timed out.")
    internal_message = "Timeout accessing %s: %s"


class HTTP(object):
    """A helper for the `requests` module."""

    @classmethod
    def get_with_timeout(cls, url, *args, **kwargs):
        """Make a GET request with timeout handling."""
        return cls.request_with_timeout("GET", url, *args, **kwargs)

    @classmethod
    def post_with_timeout(cls, url, payload, *args, **kwargs):
        """Make a POST request with timeout handling."""
        kwargs['data'] = payload
        return cls.request_with_timeout("POST", url, *args, **kwargs)

    @classmethod
    def request_with_timeout(cls, http_method, url, *args, **kwargs):
        """Call requests.request and turn a timeout into a RequestTimedOut
        exception.
        """
        return cls._request_with_timeout(
            url, requests.request, http_method, url, *args, **kwargs
        )

    @classmethod
    def _request_with_timeout(cls, url, m, *args, **kwargs):
        """Call some kind of method and turn a timeout into a RequestTimedOut
        exception.

        The core of `request_with_timeout` made easy to test.
        """
        allowed_response_codes = kwargs.get('allowed_response_codes')
        if 'allowed_response_codes' in kwargs:
            del kwargs['allowed_response_codes']
        disallowed_response_codes = kwargs.get('disallowed_response_codes')
        if 'disallowed_response_codes' in kwargs:
            del kwargs['disallowed_response_codes']

        if not 'timeout' in kwargs:
            kwargs['timeout'] = 20

        # Unicode data can't be sent over the wire. Convert it
        # to UTF-8.
        if 'data' in kwargs and isinstance(kwargs['data'], unicode):
            kwargs['data'] = kwargs.get('data').encode("utf8")
        if 'headers' in kwargs:
            headers = kwargs['headers']
            new_headers = {}
            for k, v in headers.items():
                if isinstance(k, unicode):
                    k = k.encode("utf8")
                if isinstance(v, unicode):
                    v = v.encode("utf8")
                new_headers[k] = v
            kwargs['headers'] = new_headers

        try:
            response = m(*args, **kwargs)
        except requests.exceptions.Timeout, e:
            # Wrap the requests-specific Timeout exception 
            # in a generic RequestTimedOut exception.
            raise RequestTimedOut(url, e.message)
        except requests.exceptions.RequestException, e:
            # Wrap all other requests-specific exceptions in
            # a generic RequestNetworkException.
            raise RequestNetworkException(url, e.message)

        return cls._process_response(
            url, response, allowed_response_codes, disallowed_response_codes
        )

    @classmethod
    def _process_response(cls, url, response, allowed_response_codes=None,
                          disallowed_response_codes=None):
        """Raise a RequestNetworkException if the response code indicates a
        server-side failure, or behavior so unpredictable that we can't
        continue.

        :param allowed_response_codes If passed, then only the responses with 
            http status codes in this list are processed.  The rest generate  
            BadResponseExceptions.
        :param disallowed_response_codes The values passed are added to 5xx, as 
            http status codes that would generate BadResponseExceptions.
        """
        if allowed_response_codes:
            allowed_response_codes = map(str, allowed_response_codes)
            status_code_not_in_allowed = "Got status code %%s from external server, but can only continue on: %s." % ", ".join(sorted(allowed_response_codes))
        if disallowed_response_codes:
            disallowed_response_codes = map(str, disallowed_response_codes)
        else:
            disallowed_response_codes = []

        code = response.status_code
        series = "%sxx" % (code / 100)
        code = str(code)

        if allowed_response_codes and (
                code in allowed_response_codes 
                or series in allowed_response_codes
        ):
            # The code or series has been explicitly allowed. Allow
            # the request to be processed.
            return response

        error_message = None
        if (series == '5xx' or code in disallowed_response_codes
            or series in disallowed_response_codes
        ):
            # Unless explicitly allowed, the 5xx series always results in
            # an exception.
            error_message = BadResponseException.BAD_STATUS_CODE_MESSAGE
        elif (allowed_response_codes and not (
                code in allowed_response_codes 
                or series in allowed_response_codes
        )):
            error_message = status_code_not_in_allowed

        if error_message:
            raise BadResponseException(
                url,
                error_message % code, 
                status_code=code,
                debug_message="Response content: %s" % response.content
            )
        return response

