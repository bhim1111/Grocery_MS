import json
import logging
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render, redirect
from django.urls import reverse
from django.template.loader import get_template
from customers.models import Customer
from products.models import Product
from .models import Sale, SaleDetail

logger = logging.getLogger(__name__)


def is_ajax(request):
    return request.META.get('HTTP_X_REQUESTED_WITH') == 'XMLHttpRequest'


def _money(value):
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@login_required(login_url="/accounts/login/")
def sales_list_view(request):
    context = {
        "active_icon": "sales",
        "sales": Sale.objects.all()
    }
    return render(request, "sales/sales.html", context=context)


@login_required(login_url="/accounts/login/")
def sales_add_view(request):
    context = {
        "active_icon": "sales",
        "customers": [c.to_select2() for c in Customer.objects.all()]
    }

    if request.method == 'POST' and is_ajax(request=request):
        try:
            data = json.loads(request.body)
            customer_id = int(data["customer"])
            tax_percentage = Decimal(str(data["tax_percentage"]))
            amount_paid = Decimal(str(data["amount_payed"]))
            submitted_products = data["products"]
        except (json.JSONDecodeError, KeyError, TypeError, ValueError, InvalidOperation):
            return JsonResponse({"error": "Please provide valid checkout details."}, status=400)

        if not tax_percentage.is_finite() or not Decimal("0") <= tax_percentage <= Decimal("100"):
            return JsonResponse({"error": "Tax must be between 0 and 100 percent."}, status=400)
        if not amount_paid.is_finite() or amount_paid < 0:
            return JsonResponse({"error": "Amount received must be zero or more."}, status=400)
        amount_paid = _money(amount_paid)
        if not isinstance(submitted_products, list) or not submitted_products:
            return JsonResponse({"error": "Add at least one product to the bill."}, status=400)

        quantities = {}
        try:
            for item in submitted_products:
                product_id = int(item["id"])
                quantity = item["quantity"]
                if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
                    raise ValueError
                quantities[product_id] = quantities.get(product_id, 0) + quantity
        except (KeyError, TypeError, ValueError):
            return JsonResponse({"error": "Each bill item needs a valid product and quantity."}, status=400)

        customer = Customer.objects.filter(pk=customer_id).first()
        if customer is None:
            return JsonResponse({"error": "Select a valid customer."}, status=400)

        products = Product.objects.filter(
            pk__in=quantities,
            status="ACTIVE",
        ).in_bulk()
        if len(products) != len(quantities):
            return JsonResponse({"error": "One or more products are unavailable."}, status=400)

        details = []
        subtotal = Decimal("0.00")
        for product_id, quantity in quantities.items():
            product = products[product_id]
            price = Decimal(str(product.price))
            line_total = _money(price * quantity)
            subtotal += line_total
            details.append((product, price, quantity, line_total))

        subtotal = _money(subtotal)
        tax_amount = _money(subtotal * tax_percentage / Decimal("100"))
        grand_total = subtotal + tax_amount
        if amount_paid < grand_total:
            return JsonResponse(
                {"error": "Amount received cannot be less than the bill total."},
                status=400,
            )
        change = amount_paid - grand_total

        with transaction.atomic():
            new_sale = Sale.objects.create(
                customer=customer,
                sub_total=float(subtotal),
                grand_total=float(grand_total),
                tax_amount=float(tax_amount),
                tax_percentage=float(tax_percentage),
                amount_payed=float(amount_paid),
                amount_change=float(change),
            )
            SaleDetail.objects.bulk_create([
                SaleDetail(
                    sale=new_sale,
                    product=product,
                    price=float(price),
                    quantity=quantity,
                    total_detail=float(line_total),
                )
                for product, price, quantity, line_total in details
            ])

        return JsonResponse({
            "sale_id": new_sale.id,
            "redirect_url": reverse("sales:sales_receipt", args=[new_sale.id]),
        })

    return render(request, "sales/sales_add.html", context=context)



@login_required(login_url="/accounts/login/")
def sales_details_view(request, sale_id):
    """
    Args:
        request:
        sale_id: ID of the sale to view
    """
    try:
        # Get the sale
        sale = Sale.objects.get(id=sale_id)

        # Get the sale details
        details = SaleDetail.objects.filter(sale=sale)

        context = {
            "active_icon": "sales",
            "sale": sale,
            "details": details,
        }
        return render(request, "sales/sales_details.html", context=context)
    except Sale.DoesNotExist:
        messages.success(
            request, 'There was an error getting the sale!', extra_tags="danger")
        return redirect('sales:sales_list')


@login_required(login_url="/accounts/login/")
def receipt_view(request, sale_id):
    sale = Sale.objects.get(id=sale_id)
    details = SaleDetail.objects.filter(sale=sale).select_related("product")
    return render(request, "sales/sales_receipt_pdf.html", {
        "sale": sale,
        "details": details,
    })


@login_required(login_url="/accounts/login/")
def receipt_pdf_view(request, sale_id):
    """
    Args:
        request:
        sale_id: ID of the sale to view the receipt
    """
    try:
        from weasyprint import HTML
    except (ImportError, OSError):
        logger.exception("WeasyPrint could not load its native dependencies.")
        return HttpResponse(
            "PDF receipt generation is unavailable because WeasyPrint's "
            "native dependencies could not be loaded. See the installation "
            "instructions in the project README.",
            status=503,
            content_type="text/plain",
        )

    # Get the sale
    sale = Sale.objects.get(id=sale_id)

    # Get the sale details
    details = SaleDetail.objects.filter(sale=sale)

    template = get_template("sales/sales_receipt_pdf.html")
    context = {
        "sale": sale,
        "details": details
    }
    html_template = template.render(context)

    # Create the pdf
    pdf = HTML(
        string=html_template,
        base_url=request.build_absolute_uri("/"),
    ).write_pdf()

    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="receipt-{sale.id}.pdf"'
    return response
