"""Huquqiy hujjatlar — ATMOS merchant tasdiqlash uchun majburiy sahifalar.

Routes:
  GET /oferta             — Ommaviy oferta (to'lov = qabul).
  GET /tolovni-qaytarish  — To'lovni qaytarish (refund) siyosati.
  GET /maxfiylik          — Maxfiylik siyosati.

Barchasi statik server-render (DB talab qilmaydi); umumiy maket
templates/legal_base.html da.
"""
from flask import Blueprint, render_template

legal_bp = Blueprint('legal', __name__)


@legal_bp.route('/oferta')
def oferta():
    return render_template('legal_oferta.html')


@legal_bp.route('/tolovni-qaytarish')
def refund_policy():
    return render_template('legal_refund.html')


@legal_bp.route('/maxfiylik')
def privacy_policy():
    return render_template('legal_privacy.html')
