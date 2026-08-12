"""Image selection for the PDF season report.

Offline: the selector is a pure function over one BeautifulSoup <img>, so these
run against fixture markup copied from a real Wikipedia season page rather than
hitting the network.
"""

from bs4 import BeautifulSoup

from season_simulator import SeasonSimulator


def img(markup):
    return BeautifulSoup(markup, 'html.parser').find('img')


UPLOAD = '//upload.wikimedia.org/wikipedia/commons/thumb/7/75/Max.jpg'


def test_picks_the_highest_density_srcset_entry():
    """srcset is ordered by density, so the last entry is the biggest render."""
    tag = img(
        f'<img width="140" height="210" src="/w/index.php?utm_campaign=parser" '
        f'srcset="{UPLOAD}/220px-Max.jpg 1.5x, {UPLOAD}/330px-Max.jpg 2x">'
    )
    assert SeasonSimulator._usable_image_url(tag) == f'https:{UPLOAD}/330px-Max.jpg'


def test_ignores_the_tracking_redirect_in_src():
    """src is now a utm-tagged redirect, not a usable image URL."""
    tag = img(
        f'<img width="140" height="210" '
        f'src="https://en.wikipedia.org/w/index.php?utm_content=thumbnail" '
        f'srcset="{UPLOAD}/330px-Max.jpg 2x">'
    )
    url = SeasonSimulator._usable_image_url(tag)
    assert url.startswith('https://upload.wikimedia.org/')
    assert 'utm_' not in url


def test_skips_interface_icons_by_width():
    """The padlock and the wiki logo are <img> tags too."""
    tag = img('<img width="20" height="20" src="//upload.wikimedia.org/x/40px-Padlock.png">')
    assert SeasonSimulator._usable_image_url(tag) is None


def test_skips_svg_chrome():
    tag = img('<img width="140" height="11" src="//upload.wikimedia.org/static/tagline.svg">')
    assert SeasonSimulator._usable_image_url(tag) is None


def test_skips_images_hosted_off_wikimedia():
    tag = img('<img width="400" height="300" src="https://example.com/photo.jpg">')
    assert SeasonSimulator._usable_image_url(tag) is None


def test_an_unusual_thumbnail_width_is_still_accepted():
    """The old code hardcoded 220/250/300/400/500px; MediaWiki picks its own.

    A 337px thumbnail is a perfectly good photo and used to be discarded purely
    because that number was not on the list.
    """
    tag = img(f'<img width="337" height="210" srcset="{UPLOAD}/337px-Max.jpg 2x">')
    assert SeasonSimulator._usable_image_url(tag) == f'https:{UPLOAD}/337px-Max.jpg'


def test_falls_back_to_src_when_there_is_no_srcset():
    tag = img(f'<img width="330" height="210" src="{UPLOAD}/330px-Max.jpg">')
    assert SeasonSimulator._usable_image_url(tag) == f'https:{UPLOAD}/330px-Max.jpg'
