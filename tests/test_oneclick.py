from nose.tools import (
    assert_raises_regexp,
    eq_, 
    set_trace,
)

import datetime
import json
import os

from coverage import CoverageFailure

from model import (
    Edition,
    Identifier,
    Subject,
    Contributor,
    LicensePool,
    Representation,
    DeliveryMechanism,
)

from oneclick import (
    OneClickAPI,
    MockOneClickAPI,
    #BibliographicParser,
    #Axis360BibliographicCoverageProvider,
)

from util.http import (
    RemoteIntegrationException,
    HTTP,
)

from . import DatabaseTest
from scripts import RunCoverageProviderScript
from testing import MockRequestsResponse


class OneClickTest(DatabaseTest):

    def get_data(self, filename):
        path = os.path.join(
            os.path.split(__file__)[0], "files/oneclick/", filename)
        return open(path).read()


class TestOneClickAPI(OneClickTest):

    def test_create_identifier_strings(self):
        identifier = self._identifier()
        values = OneClickAPI.create_identifier_strings(["foo", identifier])
        eq_(["foo", identifier.identifier], values)


    def test_availability_exception(self):
        api = MockOneClickAPI(self._db)
        api.queue_response(500)
        assert_raises_regexp(
            RemoteIntegrationException, "Bad response from www.oneclickapi.testv1/libraries/library_id_123/search: Got status code 500 from external server, cannot continue.", 
            api.get_available
        )

    def test_search(self):
        api = MockOneClickAPI(self._db)
        data = self.get_data("available_response_single_book_1.json")
        api.queue_response(status_code=200, content=data)

        response = api.search(mediatype='ebook', author="Alexander Mccall Smith", title="Tea Time for the Traditionally Built")
        response_dictionary = response.json()
        eq_(1, response_dictionary['pageCount'])
        eq_(u'Tea Time for the Traditionally Built', response_dictionary['items'][0]['item']['title'])

    def test_get_available(self):
        api = MockOneClickAPI(self._db)
        data = self.get_data("available_response_five_books_1.json")
        api.queue_response(status_code=200, content=data)

        response_dictionary = api.get_available()
        eq_(1, response_dictionary['pageCount'])
        eq_(5, response_dictionary['resultSetCount'])
        eq_(5, len(response_dictionary['items']))
        returned_titles = [iteminterest['item']['title'] for iteminterest in response_dictionary['items']]
        assert (u'Unusual Uses for Olive Oil' in returned_titles)




class TestParsers(OneClickTest):

    def test_bibliographic_parser(self):
        """Make sure the bibliographic information gets properly
        collated in preparation for creating Edition objects.
        """
        data = self.get_data("tiny_collection.xml")

        [bib1, av1], [bib2, av2] = BibliographicParser(
            False, True).process_all(data)

        # We didn't ask for availability information, so none was provided.
        eq_(None, av1)
        eq_(None, av2)

        eq_(u'Faith of My Fathers : A Family Memoir', bib1.title)
        eq_('eng', bib1.language)
        eq_(datetime.datetime(2000, 3, 7, 0, 0), bib1.published)

        eq_(u'Simon & Schuster', bib2.publisher)
        eq_(u'Pocket Books', bib2.imprint)

        # TODO: Would be nicer if we could test getting a real value
        # for this.
        eq_(None, bib2.series)

        # Book #1 has a primary author and another author.
        [cont1, cont2] = bib1.contributors
        eq_("McCain, John", cont1.sort_name)
        eq_([Contributor.PRIMARY_AUTHOR_ROLE], cont1.roles)

        eq_("Salter, Mark", cont2.sort_name)
        eq_([Contributor.AUTHOR_ROLE], cont2.roles)

        # Book #2 only has a primary author.
        [cont] = bib2.contributors
        eq_("Pollero, Rhonda", cont.sort_name)
        eq_([Contributor.PRIMARY_AUTHOR_ROLE], cont.roles)

        axis_id, isbn = sorted(bib1.identifiers, key=lambda x: x.identifier)
        eq_(u'0003642860', axis_id.identifier)
        eq_(u'9780375504587', isbn.identifier)

        # Check the subjects for #2 because it includes an audience,
        # unlike #1.
        subjects = sorted(bib2.subjects, key = lambda x: x.identifier)
        eq_([Subject.BISAC, Subject.BISAC, Subject.BISAC, 
             Subject.AXIS_360_AUDIENCE], [x.type for x in subjects])
        general_fiction, women_sleuths, romantic_suspense, adult = [
            x.identifier for x in subjects]
        eq_(u'FICTION / General', general_fiction)
        eq_(u'FICTION / Mystery & Detective / Women Sleuths', women_sleuths)
        eq_(u'FICTION / Romance / Suspense', romantic_suspense)
        eq_(u'General Adult', adult)

        '''
        TODO:  Perhaps want to test formats separately.
        [format] = bib1.formats
        eq_(Representation.EPUB_MEDIA_TYPE, format.content_type)
        eq_(DeliveryMechanism.ADOBE_DRM, format.drm_scheme)

        # The second book is only available in 'Blio' format, which
        # we can't use.
        eq_([], bib2.formats)
        '''


    def test_parse_author_role(self):
        """Suffixes on author names are turned into roles."""
        author = "Dyssegaard, Elisabeth Kallick (TRN)"
        parse = BibliographicParser.parse_contributor
        c = parse(author)
        eq_("Dyssegaard, Elisabeth Kallick", c.sort_name)
        eq_([Contributor.TRANSLATOR_ROLE], c.roles)

        # A corporate author is given a normal author role.
        author = "Bob, Inc. (COR)"
        c = parse(author, primary_author_found=False)
        eq_("Bob, Inc.", c.sort_name)
        eq_([Contributor.PRIMARY_AUTHOR_ROLE], c.roles)

        c = parse(author, primary_author_found=True)
        eq_("Bob, Inc.", c.sort_name)
        eq_([Contributor.AUTHOR_ROLE], c.roles)

        # An unknown author type is given an unknown role
        author = "Eve, Mallory (ZZZ)"
        c = parse(author, primary_author_found=False)
        eq_("Eve, Mallory", c.sort_name)
        eq_([Contributor.UNKNOWN_ROLE], c.roles)


    def test_availability_parser(self):
        """Make sure the availability information gets properly
        collated in preparation for updating a LicensePool.
        """

        data = self.get_data("tiny_collection.xml")

        [bib1, av1], [bib2, av2] = BibliographicParser(
            True, False).process_all(data)

        # We didn't ask for bibliographic information, so none was provided.
        eq_(None, bib1)
        eq_(None, bib2)

        eq_("0003642860", av1.primary_identifier.identifier)
        eq_(9, av1.licenses_owned)
        eq_(9, av1.licenses_available)
        eq_(0, av1.patrons_in_hold_queue)



class TestOneClickBibliographicCoverageProvider(OneClickTest):
    """Test the code that looks up bibliographic information from Axis 360."""

    def test_script_instantiation(self):
        """Test that RunCoverageProviderScript can instantiate
        the coverage provider.
        """
        api = MockOneClickAPI(self._db)
        script = RunCoverageProviderScript(
            Axis360BibliographicCoverageProvider, self._db, [],
            axis_360_api=api
        )
        assert isinstance(script.provider, 
                          Axis360BibliographicCoverageProvider)
        eq_(script.provider.api, api)


    def test_process_item_creates_presentation_ready_work(self):
        """Test the normal workflow where we ask Axis for data,
        Axis provides it, and we create a presentation-ready work.
        """
        api = MockOneClickAPI(self._db)
        data = self.get_data("single_item.xml")
        api.queue_response(200, content=data)
        
        # Here's the book mentioned in single_item.xml.
        identifier = self._identifier(identifier_type=Identifier.AXIS_360_ID)
        identifier.identifier = '0003642860'

        # This book has no LicensePool.
        eq_(None, identifier.licensed_through)

        # Run it through the Axis360BibliographicCoverageProvider
        provider = OneClickBibliographicCoverageProvider(
            self._db, axis_360_api=api
        )
        [result] = provider.process_batch([identifier])
        eq_(identifier, result)

        # A LicensePool was created. We know both how many copies of this
        # book are available, and what formats it's available in.
        pool = identifier.licensed_through
        eq_(9, pool.licenses_owned)
        [lpdm] = pool.delivery_mechanisms
        eq_('application/epub+zip (vnd.adobe/adept+xml)', 
            lpdm.delivery_mechanism.name)

        # A Work was created and made presentation ready.
        eq_('Faith of My Fathers : A Family Memoir', pool.work.title)
        eq_(True, pool.work.presentation_ready)
       
    def test_transient_failure_if_requested_book_not_mentioned(self):
        """Test an unrealistic case where we ask Axis 360 about one book and
        it tells us about a totally different book.
        """
        api = MockOneClickAPI(self._db)

        # We're going to ask about abcdef
        identifier = self._identifier(identifier_type=Identifier.AXIS_360_ID)
        identifier.identifier = 'abcdef'

        # But we're going to get told about 0003642860.
        data = self.get_data("single_item.xml")
        api.queue_response(200, content=data)
        
        # Run abcdef through the Axis360BibliographicCoverageProvider.
        provider = Axis360BibliographicCoverageProvider(
            self._db, axis_360_api=api
        )
        [result] = provider.process_batch([identifier])

        # Coverage failed for the book we asked about.
        assert isinstance(result, CoverageFailure)
        eq_(identifier, result.obj)
        eq_("Book not in collection", result.exception)
        
        # And nothing major was done about the book we were told
        # about. We created an Identifier record for its identifier,
        # but no LicensePool or Edition.
        wrong_identifier = Identifier.for_foreign_id(
            self._db, Identifier.AXIS_360_ID, "0003642860"
        )
        eq_(None, identifier.licensed_through)
        eq_([], identifier.primarily_identifies)
