from django.conf import settings
import pathlib
from markdown import markdown
from decimal import Decimal

product_logo_meta = "tillweb:product-logo"
tasting_notes_meta = "tillweb:tasting-notes"

MEDIA_DIR = pathlib.Path(settings.MEDIA_ROOT) / "emf"
LOGO_DIR = MEDIA_DIR / "product-logos"
LOGO_PREFIX = "/media/emf/product-logos/"

LOGO_DIR.mkdir(parents=True, exist_ok=True)


# Lookup table from StockLine.location to displayed location; used for
# stockline.location_display:

robot_arms = 'Robot Arms'
cybar = 'Cybar'

locations = {
    'Bar': robot_arms,
    'Fridge': robot_arms,
    'Back bar': robot_arms,
    'Counter': robot_arms,
    'Optics (main bar)': robot_arms,
    'Null Sector': cybar,
    'Optics (Null Sector)': cybar,
}


# Convert various till models to dicts to output as json

def department_to_dict(d):
    return {
        'type': 'department',
        'id': d.id,
        'description': d.description,
        'notes': d.notes,
    }


def stockline_to_dict(line, brief=False):
    # If regular, return stockitem or null
    # If display [not supported]
    # If continuous, return stocktype
    d = {
        'type': 'stockline-brief' if brief else 'stockline',
        'key': f'stockline/{line.id}',
        'id': line.id,
        'name': line.name,
        'location': line.location,
        'location_display': locations.get(line.location, line.location),
        'note': line.note,
        'linetype': line.linetype,
    }
    if not brief:
        d['stockitem'] = stockitem_to_dict(line.stockonsale[0]) \
            if line.linetype == "regular" and line.stockonsale else None
        d['stocktype'] = stocktype_to_dict(line.stocktype) \
            if line.linetype == "continuous" else None
    return d


def stocktype_to_dict(s):
    logo = None
    tasting_notes = None
    if product_logo_meta in s.meta:
        # We're making an assumption about where media is, here
        logofile = f"{s.meta[product_logo_meta].document_hash.hex()}.png"
        logopath = LOGO_DIR / logofile
        if not logopath.exists():
            with open(logopath, 'wb') as f:
                f.write(s.meta[product_logo_meta].document)
        logo = LOGO_PREFIX + logofile
    if tasting_notes_meta in s.meta:
        tasting_notes = markdown(s.meta[tasting_notes_meta].value)

    # StockType.stocklines is a list of all StockLine objects that
    # link to this StockType. Filter this list for continuous
    # stocklines to find stocklines that are actually selling this
    # stocktype. Add stocklines explicitly selling stock items of this
    # type.
    stocklines = [sl for sl in s.stocklines if sl.linetype == "continuous"] \
        + [si.stockline for si in s.items if si.stockline]

    return {
        'type': 'stocktype',
        'key': f'stocktype/{s.id}',
        'id': s.id,
        'department': department_to_dict(s.department),
        'manufacturer': s.manufacturer,
        'name': s.name,
        'abv': s.abv,
        'fullname': format(s),
        'price': s.saleprice,
        'logo': logo,
        'tasting_notes': tasting_notes,
        'base_units_bought': s.total,
        'base_units_remaining': s.total_remaining,
        'base_unit_name': s.unit.name,
        'sale_unit_name': s.unit.sale_unit_name,
        'sale_unit_name_plural': s.unit.sale_unit_name_plural,
        'base_units_per_sale_unit': s.unit.base_units_per_sale_unit,
        'stock_unit_name': s.unit.stock_unit_name,
        'stock_unit_name_plural': s.unit.stock_unit_name_plural,
        'base_units_per_stock_unit': s.unit.base_units_per_stock_unit,
        'stocklines': [stockline_to_dict(sl, brief=True) for sl in stocklines],
    }


def stockitem_to_dict(s):
    return {
        'type': 'stockitem',
        'key': f'stockitem/{s.id}',
        'id': s.id,
        'stocktype': stocktype_to_dict(s.stocktype),
        'description': s.description,
        'remaining': s.remaining,
        'size': s.size,
        'remaining_pct': (s.remaining / s.size * 100).quantize(Decimal("0.01")),
    }


def plu_to_dict(plu):
    return {
        'type': 'plu',
        'id': plu.id,
        'description': plu.description,
        'note': plu.note,
        'department': department_to_dict(plu.department),
        'price': plu.price,
    }
