from django.contrib import admin
from .models import Session, Page, DisplayPage


class SessionAdmin(admin.ModelAdmin):
    list_display = ('opening_time', 'closing_time', 'weight', 'comment')
    list_editable = ('weight', 'comment')


admin.site.register(Session, SessionAdmin)


class PageAdmin(admin.ModelAdmin):
    list_display = ('title', 'path')


admin.site.register(Page, PageAdmin)


class DisplayPageAdmin(admin.ModelAdmin):
    list_display = ('slug', 'order', 'enabled', 'condition',
                    'display_after', 'display_until', 'title')
    list_filter = ('priority', )
    list_editable = ('order', 'enabled', 'condition', 'display_after',
                     'display_until', 'title')


admin.site.register(DisplayPage, DisplayPageAdmin)
