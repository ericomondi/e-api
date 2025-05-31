import os
import base64
import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Dict, Any
from datetime import datetime
from database import db_dependency
from models import Transaction, TransactionStatus
from dotenv import load_dotenv
from decimal import Decimal

load_dotenv()

router = APIRouter(prefix="/lnmo", tags=["lnmo"])

class TransactionRequest(BaseModel):
    Amount: Decimal = Field(..., gt=0)
    PhoneNumber: str = Field(..., min_length=10, max_length=15)
    AccountReference: str = Field(..., min_length=1, max_length=100)

class APIResponse(BaseModel):
    status: str
    message: str
    data: Dict[Any, Any] = {}

class LNMORepository:
    def __init__(self):
        self.MPESA_LNMO_CONSUMER_KEY = os.getenv("MPESA_LNMO_CONSUMER_KEY")
        self.MPESA_LNMO_CONSUMER_SECRET = os.getenv("MPESA_LNMO_CONSUMER_SECRET")
        self.MPESA_LNMO_ENVIRONMENT = os.getenv("MPESA_LNMO_ENVIRONMENT", "sandbox")
        self.MPESA_LNMO_PASS_KEY = os.getenv("MPESA_LNMO_PASS_KEY")
        self.MPESA_LNMO_SHORT_CODE = os.getenv("MPESA_LNMO_SHORT_CODE")
        self.CALLBACK_URL = os.getenv("MPESA_CALLBACK_URL")

    def transact(self, data: dict, db: db_dependency) -> Dict[str, Any]:
        endpoint = f"https://{self.MPESA_LNMO_ENVIRONMENT}.safaricom.co.ke/mpesa/stkpush/v1/processrequest"
        headers = {
            "Authorization": "Bearer " + self.generate_access_token(),
            "Content-Type": "application/json",
        }
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        payload = {
            "BusinessShortCode": self.MPESA_LNMO_SHORT_CODE,
            "Password": self.generate_password(timestamp),
            "Timestamp": timestamp,
            "TransactionType": "CustomerPayBillOnline",
            "Amount": str(data["Amount"]),
            "PartyA": data["PhoneNumber"],
            "PartyB": self.MPESA_LNMO_SHORT_CODE,
            "PhoneNumber": data["PhoneNumber"],
            "CallBackURL": self.CALLBACK_URL,
            "AccountReference": data["AccountReference"],
            "TransactionDesc": "Payment for order " + data["AccountReference"],
        }
        response = requests.post(endpoint, json=payload, headers=headers)
        response_data = response.json()

        transaction = Transaction(
            _pid=data["AccountReference"],
            party_a=data["PhoneNumber"],
            party_b=self.MPESA_LNMO_SHORT_CODE,
            account_reference=data["AccountReference"],
            transaction_category=0,  # PURCHASE_ORDER
            transaction_type=1,      # CREDIT
            transaction_channel=1,   # LNMO
            transaction_aggregator=0, # MPESA_KE
            transaction_id=response_data.get("CheckoutRequestID"),
            transaction_amount=data["Amount"],
            transaction_code=None,
            transaction_timestamp=datetime.now(),
            transaction_details="Payment for order " + data["AccountReference"],
            _feedback=response_data,
            _status=TransactionStatus.PROCESSING
        )
        db.add(transaction)
        db.commit()
        db.refresh(transaction)
        return response_data

    def generate_access_token(self) -> str:
        endpoint = f"https://{self.MPESA_LNMO_ENVIRONMENT}.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials"
        credentials = f"{self.MPESA_LNMO_CONSUMER_KEY}:{self.MPESA_LNMO_CONSUMER_SECRET}"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()
        headers = {"Authorization": f"Basic {encoded_credentials}"}
        response = requests.get(endpoint, headers=headers)
        return response.json().get("access_token")

    def generate_password(self, timestamp: str) -> str:
        password_str = f"{self.MPESA_LNMO_SHORT_CODE}{self.MPESA_LNMO_PASS_KEY}{timestamp}"
        return base64.b64encode(password_str.encode()).decode()

@router.post("/transact", response_model=APIResponse)
def transact(transaction_data: TransactionRequest, db: db_dependency):
    try:
        data = {
            "Amount": transaction_data.Amount,
            "PhoneNumber": transaction_data.PhoneNumber,
            "AccountReference": transaction_data.AccountReference
        }
        lnmo_repo = LNMORepository()
        response = lnmo_repo.transact(data, db)
        return APIResponse(status="info", message="Transaction processing", data=response)
    except Exception as e:
        raise HTTPException(status_code=400, detail={"status": "danger", "message": str(e)})

@router.post("/callback")
def callback(data: Dict[str, Any], db: db_dependency):
    checkout_request_id = data.get("Body", {}).get("stkCallback", {}).get("CheckoutRequestID")
    result_code = data.get("Body", {}).get("stkCallback", {}).get("ResultCode")
    transaction = db.query(Transaction).filter(Transaction.transaction_id == checkout_request_id).first()
    if transaction:
        if result_code == "0":  # Success
            transaction._status = TransactionStatus.ACCEPTED
        else:
            transaction._status = TransactionStatus.REJECTED
        db.commit()
    return {"message": "Callback received"}