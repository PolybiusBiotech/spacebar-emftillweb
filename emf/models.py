from django.db import models
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
import markdown as markdown_module
from django.utils.safestring import mark_safe
from django.urls import reverse
from .tilldb import tillsession
from .display import displays


# XXX be careful to refer to this explicitly, otherwise you might get
# the quicktill.models.Session object instead!
class Session(models.Model):
    """When we're open
    """
    opening_time = models.DateTimeField(help_text="Time we open")
    closing_time = models.DateTimeField(help_text="Time we close")
    weight = models.FloatField(help_text="Expected busyness of session")
    comment = models.CharField(
        max_length=100, blank=True,
        help_text="Displayed on web interface; not private!")

    class Meta:
        ordering = ('opening_time',)
        constraints = (
            models.CheckConstraint(
                condition=models.Q(closing_time__gt=models.F('opening_time')),
                name="ends_after_start"),
        )

    def __str__(self):
        return f"{self.opening_time:} – {self.closing_time}"

    @property
    def length(self):
        return self.closing_time - self.opening_time

    def clean(self):
        if self.opening_time >= self.closing_time:
            raise ValidationError(_("Session must end after it starts."))


class Page(models.Model):
    """A page of content for the website
    """
    path = models.CharField(
        max_length=80, unique=True, blank=True,
        help_text="Used to form the URL for the page. Lower-case, no spaces, "
        "do not include leading or trailing '/'. Blank for front page.")

    title = models.CharField(max_length=200)

    content = models.TextField()

    def get_absolute_url(self):
        return f"/{self.path}/"

    def as_html(self):
        return mark_safe(
            markdown_module.markdown(
                self.content,
                extensions=["def_list"]))

    def __str__(self):
        return f"{self.path}/{self.title}"


class DisplayPage(models.Model):
    """A page for the display boards
    """
    slug = models.SlugField(
        max_length=80, unique=True,
        help_text="Internal name for the page. If multiple pages of "
        "the same order and priority are being displayed, they will be "
        "shown in alphanumeric order by name.")
    order = models.IntegerField(
        default=1000, help_text="Hint for ordering of pages in the carousel")

    # Conditions for display as part of the carousel
    enabled = models.BooleanField(
        default=False,
        help_text="Include this page in the carousel?")
    CONDITION_CHOICES = (
        ('A', 'Always'),
        ('O', 'Only when open'),
        ('C', 'Only when closed'),
    )
    condition = models.CharField(
        max_length=1, choices=CONDITION_CHOICES, default='A',
        help_text="When should this page be displayed?")
    display_after = models.DateTimeField(
        blank=True, null=True, help_text="Don't display this page until "
        "after this time")
    display_until = models.DateTimeField(
        blank=True, null=True, help_text="Stop displaying this page after "
        "this time")
    display_time = models.IntegerField(
        default=30, help_text="Display the page for this number of seconds")
    PRIORITY_CHOICES = (
        ('U', 'Urgent'),
        ('N', 'Normal'),
    )
    priority = models.CharField(
        max_length=1, choices=PRIORITY_CHOICES, default='N', blank=False,
        help_text="Priority for this page. Urgent pages suppress the display "
        "of all other non-urgent pages.")

    # Content for the page
    title = models.CharField(
        max_length=80, blank=True,
        help_text="Title to be shown at the top of the display, between "
        "the logo and the clock")

    TEMPLATE_CHOICES = tuple(
        (k, v.description) for k, v in displays.items())
    template = models.CharField(
        max_length=80, blank=True,
        choices=TEMPLATE_CHOICES,
        help_text="Programmed template for the page")
    content = models.TextField(
        blank=True,
        help_text="Content for the page if no template is chosen. "
        "Markdown or HTML")

    class Meta:
        ordering = ('order', 'slug')

    def __str__(self):
        return self.slug

    def get_absolute_url(self):
        return reverse('display-page', args=[self.slug])

    def render_content(self):
        if self.template:
            cls = displays[self.template]
            with tillsession() as s:
                return cls(s).text

        return mark_safe(
            markdown_module.markdown(
                self.content,
                extensions=["def_list"]))

    def as_dict(self):
        return {
            'name': self.slug,
            'header': self.title,
            'content': self.render_content,
            'duration': self.display_time,
        }
