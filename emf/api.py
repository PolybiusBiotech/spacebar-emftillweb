from .tilldb import tillsession, on_tap
import json
from quicktill.models import StockType, Unit, Department
from quicktill.models import StockLine
from sqlalchemy.orm import undefer, joinedload
from django.conf import settings
from django.http import JsonResponse, Http404, HttpResponse
from django.http import HttpResponseBadRequest, HttpResponseForbidden
from django.http import HttpResponseNotAllowed
from django.views.decorators.csrf import csrf_exempt
from .api_objects import department_to_dict, stocktype_to_dict, \
    stockitem_to_dict, stockline_to_dict


# Partial queries

def stocktype_query(s):
    return s.query(StockType)\
            .join(Unit)\
            .filter(StockType.total_remaining > 0)\
            .order_by(StockType.dept_id)\
            .order_by(StockType.manufacturer)\
            .order_by(StockType.name)\
            .options(undefer('total'))\
            .options(undefer('total_remaining'))\


# Views


def departments(request):
    with tillsession() as s:
        depts = s.query(Department).order_by(Department.id).all()

        return JsonResponse({
            'departments': [department_to_dict(d) for d in depts],
        })


def api_on_tap(request):
    with tillsession() as s:
        ales, kegs, ciders = on_tap(s)

        return JsonResponse({
            'ales': [stockitem_to_dict(ale) for ale, _ in ales],
            'kegs': [stockitem_to_dict(keg) for keg, _ in kegs],
            'ciders': [stockitem_to_dict(cider) for cider, _ in ciders],
        })


def api_cybar_on_tap(request):
    with tillsession() as s:
        _, kegs, ciders = on_tap(s, location="Null Sector")

        return JsonResponse({
            'kegs': [stockitem_to_dict(keg) for keg, _ in kegs],
            'ciders': [stockitem_to_dict(cider) for cider, _ in ciders],
        })


def stock(request):
    with tillsession() as s:
        stocktypes = stocktype_query(s).all()

        return JsonResponse({
            'stocktypes': [stocktype_to_dict(st) for st in stocktypes],
        })


def dept(request, dept_id):
    with tillsession() as s:
        stocktypes = stocktype_query(s)\
            .filter(StockType.dept_id == dept_id)\
            .all()

        return JsonResponse({
            'stocktypes': [stocktype_to_dict(st) for st in stocktypes],
        })


def stocktype(request, stocktype_id):
    with tillsession() as s:
        stocktype = s.query(StockType)\
                     .options(joinedload('unit'))\
                     .options(undefer('total'))\
                     .options(undefer('total_remaining'))\
                     .get(stocktype_id)

        if not stocktype:
            raise Http404

        return JsonResponse(stocktype_to_dict(stocktype))


def locations(request):
    with tillsession() as s:
        locations = StockLine.locations(s)

    return JsonResponse({
        'locations': locations,
    })


def stocklines(request):
    brief = request.GET.get('output', 'brief') != 'full'
    with tillsession() as s:
        q = s.query(StockLine)\
             .order_by(StockLine.location, StockLine.name)
        if not brief:
            q = q.options(joinedload("stockonsale"),
                          joinedload("stockonsale.stocktype"),
                          joinedload("stockonsale.stocktype.meta"),
                          undefer("stockonsale.remaining"),
                          undefer("stockonsale.stocktype.total_remaining"),
                          undefer("stockonsale.stocktype.total"))
        if 'type' in request.GET:
            q = q.filter(StockLine.linetype.in_(request.GET.getlist('type')))
        if 'location' in request.GET:
            q = q.filter(StockLine.location.in_(
                request.GET.getlist('location')))
        if 'startswith' in request.GET:
            q = q.filter(StockLine.name.ilike(request.GET['startswith'] + '%'))
        stocklines = q.all()

        return JsonResponse({
            'stocklines': [stockline_to_dict(sl, brief=brief)
                           for sl in stocklines],
        })


# Private API method to allow tapboard to set the note on a stockline
@csrf_exempt
def stockline_set_note(request, stockline_id):
    with tillsession() as s:
        sl = s.query(StockLine).get(stockline_id)
        if not sl:
            raise Http404
        if request.method != 'POST':
            return HttpResponseNotAllowed(['POST'])
        try:
            req = json.loads(request.body)
        except Exception:
            return HttpResponseBadRequest("JSON required")
        if settings.DEBUG:
            password = "test"
        else:
            password = settings.LINE_NOTE_PASSWORD
        if "password" not in req or not password or req['password'] != password:
            return HttpResponseForbidden()
        sl.note = req.get("note", "")
        s.commit()
        return HttpResponse("Note set")
