"""The cover letter as a one-page A4 PDF, laid out as a letter with the CV's font and colour.

templates/letter.html.j2 and Chromium (applai.cv.print_pdf) render it: the candidate's name and
contact details, place and date in the offer's language, the company as recipient, the subject
with the offer's title, then the checked letter as it is (greeting, paragraphs, sign-off, name).
"""

from datetime import date

from jinja2 import Environment, PackageLoader

from applai.cv import Lang, RenderedCv, print_pdf
from applai.profile import Identity

_MARGINS_MM = (20, 22, 20, 22)  # top, right, bottom, left
_MONTHS = {
    "fr": ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre",
           "octobre", "novembre", "décembre"),
    "en": ("January", "February", "March", "April", "May", "June", "July", "August", "September",
           "October", "November", "December"),
}  # fmt: skip
_SUBJECT = {"fr": "Objet :", "en": "Subject:"}


def render_letter(
    identity: Identity, company: str, title: str, text: str, lang: Lang, day: date
) -> RenderedCv:
    """The PDF of the letter text (paragraphs split by a blank line) written on day."""
    date_text = f"{day.day} {_MONTHS[lang][day.month - 1]} {day.year}"
    paragraphs = [block for block in text.strip().split("\n\n") if block.strip()]
    environment = Environment(loader=PackageLoader("applai"), autoescape=True)
    html = environment.get_template("letter.html.j2").render(
        lang=lang,
        margins=" ".join(f"{margin}mm" for margin in _MARGINS_MM),
        name=f"{identity.first_name} {identity.last_name}",
        email=identity.email,
        phone=identity.phone,
        city=identity.city,
        place_date=f"{identity.city}, {'le ' if lang == 'fr' else ''}{date_text}",
        company=company,
        subject=f"{_SUBJECT[lang]} {title}",
        paragraphs=paragraphs,
    )
    return print_pdf(html, _MARGINS_MM)
