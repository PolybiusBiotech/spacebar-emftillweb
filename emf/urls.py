from django.urls import path
from django.views.generic.base import RedirectView
from emf import views
from emf import api
from emf import kiosk

urls = [
    path('', views.frontpage, name="frontpage"),
    path('favicon.ico', RedirectView.as_view(url='/static/favicon.ico')),
    path('prices/', views.pricelist, name="pricelist"),
    path('product/<stocktypeid>/', views.product, name="product"),
    path('database-dump/emfcamp.sql.gz', views.database_dump,
         name="emf-database-dump"),

    path('display/', views.display, name="display"),
    path('display/info.json', views.display_info, name="display-info"),
    path('display/<page>/', views.display, name="display-page"),
    path('display/<page>/info.json', views.display_page_info,
         name="display-page-info"),

    path('tapboard/', views.tapboard, name="tapboard"),
    path('tapboard/sw.js', views.tapboard_sw, name="tapboard-sw"),

    path('cellarboard/', views.cellarboard, kwargs={'location': 'Bar'}),
    path('cellarboard/<location>/', views.cellarboard, name="cellarboard"),

    path('barboard/', views.barboard, kwargs={'location': 'Bar'}),
    path('barboard/<location>/', views.barboard, name="barboard"),

    path('misc/jontyfacts/', views.jontyfacts, name="jontyfacts"),
    path('misc/stocktypes/', views.stocktypes, name="stocktypes"),

    path('tillprofile/', views.tillprofile, name="till-profile"),

    path('api/sessions.json', views.api_sessions, name="api-sessions"),
    path('api/progress.json', views.api_progress, name="api-progress"),
    path('api/departments.json', api.departments, name="api-departments"),
    path('api/on-tap.json', api.api_on_tap, name="api-on-tap"),
    path('api/cybar-on-tap.json', api.api_cybar_on_tap,
         name="api-cybar-on-tap"),
    path('api/stocktypes.json', api.stock, name="api-stocktypes"),
    path('api/department/<int:dept_id>.json', api.dept, name="api-dept"),
    path('api/stocktype/<int:stocktype_id>.json', api.stocktype,
         name="api-stocktype"),
    path('api/locations.json', api.locations, name="api-locations"),
    path('api/stocklines.json', api.stocklines, name="api-stocklines"),
    path('api/stockline/<int:stockline_id>/set-note/', api.stockline_set_note,
         name="api-stockline-set-note"),

    path('api/kiosk/orders/', kiosk.orders, name="api-kiosk-orders"),
    path('api/kiosk/orders/<int:transid>/', kiosk.order_detail,
         name="api-kiosk-order"),
    path('api/kiosk/orders/<int:transid>/collect/', kiosk.update_order,
         name="api-kiosk-collect-order", kwargs={'action': 'collect'}),
    path('api/kiosk/orders/<int:transid>/id-reject/', kiosk.update_order,
         name="api-kiosk-reject-order", kwargs={'action': 'id-reject'}),
]
