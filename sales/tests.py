import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from customers.models import Customer
from products.models import Category, Product
from .models import Sale, SaleDetail


class BillingTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="cashier",
            password="test-password",
        )
        self.client.force_login(self.user)
        self.customer = Customer.objects.create(
            first_name="Jordan",
            last_name="Lee",
        )
        category = Category.objects.create(
            name="Drinks",
            description="Beverages",
            status="ACTIVE",
        )
        self.product = Product.objects.create(
            name="Coffee",
            description="Fresh coffee",
            status="ACTIVE",
            category=category,
            price=12.50,
        )

    def submit_bill(self, amount_paid):
        return self.client.post(
            reverse("sales:sales_add"),
            data=json.dumps({
                "customer": self.customer.id,
                "tax_percentage": 10,
                "amount_payed": amount_paid,
                "products": [{
                    "id": self.product.id,
                    "quantity": 2,
                    "price": 0.01,
                    "total_product": 0.02,
                }],
            }),
            content_type="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

    def test_checkout_page_shows_billing_controls(self):
        response = self.client.get(reverse("sales:sales_add"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Bill items")
        self.assertContains(response, "Amount received")
        self.assertContains(response, "Change due")
        self.assertContains(response, "Complete sale")

    def test_bill_uses_database_prices_and_includes_tax(self):
        response = self.submit_bill("30.00")

        self.assertEqual(response.status_code, 200)
        sale = Sale.objects.get()
        detail = SaleDetail.objects.get(sale=sale)
        self.assertEqual(sale.sub_total, 25.0)
        self.assertEqual(sale.tax_amount, 2.5)
        self.assertEqual(sale.grand_total, 27.5)
        self.assertEqual(sale.amount_change, 2.5)
        self.assertEqual(detail.price, 12.5)
        self.assertEqual(detail.total_detail, 25.0)
        self.assertEqual(
            response.json()["redirect_url"],
            reverse("sales:sales_receipt", args=[sale.id]),
        )

    def test_bill_rejects_payment_below_total(self):
        response = self.submit_bill("27.49")

        self.assertEqual(response.status_code, 400)
        self.assertIn("less than", response.json()["error"])
        self.assertFalse(Sale.objects.exists())

    def test_printable_bill_shows_print_and_pdf_actions(self):
        sale = Sale.objects.create(
            customer=self.customer,
            sub_total=25,
            grand_total=27.5,
            tax_amount=2.5,
            tax_percentage=10,
            amount_payed=30,
            amount_change=2.5,
        )
        SaleDetail.objects.create(
            sale=sale,
            product=self.product,
            price=12.5,
            quantity=2,
            total_detail=25,
        )

        response = self.client.get(reverse("sales:sales_receipt", args=[sale.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Print bill")
        self.assertContains(response, "Open PDF")
        self.assertContains(response, "Change")
        self.assertContains(response, "Rs.")
        self.assertNotContains(response, "$")

    def test_pdf_receipt_renders_or_reports_missing_native_libraries(self):
        sale = Sale.objects.create(
            customer=self.customer,
            sub_total=12.5,
            grand_total=13.75,
            tax_amount=1.25,
            tax_percentage=10,
            amount_payed=15,
            amount_change=1.25,
        )
        SaleDetail.objects.create(
            sale=sale,
            product=self.product,
            price=12.5,
            quantity=1,
            total_detail=12.5,
        )

        response = self.client.get(
            reverse("sales:sales_receipt_pdf", args=[sale.id])
        )

        if response.status_code == 503:
            self.assertContains(
                response,
                "native dependencies",
                status_code=503,
            )
        else:
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response["Content-Type"], "application/pdf")
            self.assertTrue(response.content.startswith(b"%PDF-"))
