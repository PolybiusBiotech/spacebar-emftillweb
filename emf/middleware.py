from django.shortcuts import render
from django.urls import reverse
from .models import Page
import re

page_re = re.compile(r'^/([\w|/]+)/$')


class PagesFallbackMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        # If it's not a 404, we don't need to do anything
        if response.status_code != 404:
            return response

        match = page_re.match(request.path_info)
        if not match:
            return response

        try:
            page = Page.objects.get(path=match.group(1))
        except Page.DoesNotExist:
            return response

        return render(request, 'emf/page.html', context={
            'object': page,
            'page': page,
            'admin_url': reverse(
                'admin:emf_page_change', args=(page.pk,)),
        })
