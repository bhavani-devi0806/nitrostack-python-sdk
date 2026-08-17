# Flight Booking MCP Server Example (with OAuth 2.1)
#
# This example demonstrates how to protect MCP tools using OAuth 2.1.
# The tools are protected by the @use_guards(OAuthGuard, create_scope_guard([...]))
# decorators, requiring a valid OAuth token from the configured introspection endpoint or JWKS provider.
#
# To run this example, configure the OAuth settings in your .env file:
#   PORT=3000
#   RESOURCE_URI=https://mcplocal
#   AUTH_SERVER_URL=https://dev-5dt0utuk315713tjm.us.auth0.com
#   OAUTH_INTROSPECTION_ENDPOINT=http://localhost:3000/oauth/introspect
#   # Or using JWKS:
#   # JWKS_URI=http://localhost:3000/oauth/jwks
#   # TOKEN_AUDIENCE=https://mcplocal
#   # TOKEN_ISSUER=https://dev-5dt0utuk315713tjm.us.auth0.com

import asyncio
import os
import sys
import json
import uuid
import time
import urllib.request
import urllib.parse
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field

# Ensure parent directory is in sys.path so nitrostack can be imported
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from nitrostack import (
    tool,
    prompt,
    resource,
    injectable,
    module,
    mcp_app,
    McpApplicationFactory,
    ServerConfig,
    ExecutionContext,
    use_guards,
    widget,
    OAuthGuard,
    health_check,
)
from nitrostack.auth.oauth import OAuthModule

# 1. Custom Scope Guard Factory
def create_scope_guard(required_scopes: list):
    class ScopeGuard:
        async def can_activate(self, context: ExecutionContext) -> bool:
            user_scopes = getattr(context.auth, "scopes", [])
            missing_scopes = [s for s in required_scopes if s not in user_scopes]
            if missing_scopes:
                raise ValueError(
                    f"Insufficient scope. Required: {', '.join(required_scopes)}. "
                    f"Missing: {', '.join(missing_scopes)}"
                )
            return True
    return ScopeGuard

# 2. Input Validation Schemas
class SearchFlightsInput(BaseModel):
    origin: str = Field(description="Origin airport IATA code (e.g., 'JFK', 'LHR')")
    destination: str = Field(description="Destination airport IATA code (e.g., 'LAX', 'CDG')")
    departureDate: str = Field(description="Departure date in YYYY-MM-DD format")
    returnDate: Optional[str] = Field(default=None, description="Return date in YYYY-MM-DD format for round trip")
    adults: int = Field(default=1, description="Number of adult passengers (18+)")
    cabinClass: str = Field(default="economy", description="Preferred cabin class (economy, premium_economy, business, first)")

class FlightDetailsInput(BaseModel):
    offerId: str = Field(description="The flight offer ID from search results")

class AirportSearchInput(BaseModel):
    query: str = Field(description="The search query for airports (e.g., 'London', 'New York')")

class CreateOrderInput(BaseModel):
    offerId: str = Field(description="The offer ID to book")
    passengers: str = Field(description="JSON string containing array of passenger objects. Each passenger must have: title (mr/ms/mrs/miss/dr), givenName (first name), familyName (last name), gender (M/F), bornOn (YYYY-MM-DD), email, phoneNumber.")

class OrderDetailsInput(BaseModel):
    orderId: str = Field(description="The order ID")

class SeatMapInput(BaseModel):
    offerId: str = Field(description="The offer ID to get seats for")

class CancelOrderInput(BaseModel):
    orderId: str = Field(description="The order ID to cancel")

class GetAirlinesInput(BaseModel):
    pass



# 3. Duffel Flight API Service
@injectable()
class DuffelService:
    def __init__(self):
        self.api_key = os.environ.get("DUFFEL_API_KEY")
        self.is_mock = not self.api_key or self.api_key.startswith("your-") or len(self.api_key) < 5

    def _request(self, method: str, path: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if self.is_mock:
            raise ValueError("Duffel API key is not configured. Running in mock mode.")
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Duffel-Version": "v1",
            "Content-Type": "application/json"
        }
        url = f"https://api.duffel.com{path}"
        req_data = json.dumps({"data": data}).encode("utf-8") if data else None
        
        req = urllib.request.Request(url, data=req_data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                res_payload = json.loads(response.read().decode("utf-8"))
                return res_payload.get("data", {})
        except Exception as e:
            sys.stderr.write(f"Duffel API Request failed: {e}\n")
            sys.stderr.flush()
            raise e

    async def search_flights(self, params: Dict[str, Any]) -> Dict[str, Any]:
        if self.is_mock:
            return {
                "id": "orq_mock123456",
                "offers": [
                    {
                        "id": "off_mock123456",
                        "total_amount": "450.00",
                        "total_currency": "USD",
                        "expires_at": "2026-12-31T12:00:00Z",
                        "slices": [
                            {
                                "origin": {"iata_code": params["origin"], "name": "Origin Airport", "city_name": "Origin City"},
                                "destination": {"iata_code": params["destination"], "name": "Dest Airport", "city_name": "Dest City"},
                                "duration": "PT6H30M",
                                "segments": [
                                    {
                                        "id": "seg_outbound",
                                        "origin": {"iata_code": params["origin"]},
                                        "destination": {"iata_code": params["destination"]},
                                        "departing_at": f"{params['departureDate']}T08:00:00Z",
                                        "arriving_at": f"{params['departureDate']}T14:30:00Z",
                                        "marketing_carrier": {"name": "Mock Airlines"},
                                        "marketing_carrier_flight_number": "MK123",
                                        "aircraft": {"name": "Boeing 787"}
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        
        slices = [{"origin": params["origin"], "destination": params["destination"], "departure_date": params["departureDate"]}]
        if params.get("returnDate"):
            slices.append({"origin": params["destination"], "destination": params["origin"], "departure_date": params["returnDate"]})
            
        duffel_params = {
            "slices": slices,
            "passengers": params.get("passengers", [{"type": "adult"}]),
            "cabin_class": params.get("cabinClass", "economy"),
            "return_offers": True
        }
        res = self._request("POST", "/offer_requests", duffel_params)
        return {
            "id": res.get("id"),
            "offers": res.get("offers", []),
            "passengers": res.get("passengers", []),
            "slices": res.get("slices", [])
        }

    async def get_offer(self, offer_id: str) -> Dict[str, Any]:
        if self.is_mock:
            return {
                "id": offer_id,
                "total_amount": "450.00",
                "total_currency": "USD",
                "expires_at": "2026-12-31T12:00:00Z",
                "slices": [
                    {
                        "origin": {"iata_code": "JFK", "name": "John F. Kennedy Airport", "city_name": "New York"},
                        "destination": {"iata_code": "LAX", "name": "Los Angeles Airport", "city_name": "Los Angeles"},
                        "duration": "PT6H30M",
                        "segments": [
                            {
                                "id": "seg_mock",
                                "origin": {"iata_code": "JFK"},
                                "destination": {"iata_code": "LAX"},
                                "departing_at": "2026-07-15T08:00:00Z",
                                "arriving_at": "2026-07-15T14:30:00Z",
                                "marketing_carrier": {"name": "Mock Airlines"},
                                "marketing_carrier_flight_number": "MK123",
                                "aircraft": {"name": "Boeing 787"}
                            }
                        ]
                    }
                ]
            }
        return self._request("GET", f"/offers/{offer_id}")

    async def get_seats_for_offer(self, offer_id: str) -> List[Dict[str, Any]]:
        if self.is_mock:
            return [
                {
                    "cabin_class": "economy",
                    "rows": [
                        {
                            "row_number": 10,
                            "sections": [
                                {
                                    "elements": [
                                        {
                                            "type": "seat",
                                            "id": "seat_10a",
                                            "designator": "10A",
                                            "available_services": [{"total_amount": "25.00", "total_currency": "USD"}],
                                            "disclosures": ["window"]
                                        },
                                        {
                                            "type": "seat",
                                            "id": "seat_10b",
                                            "designator": "10B",
                                            "available_services": [{"total_amount": "0.00", "total_currency": "USD"}],
                                            "disclosures": ["middle"]
                                        }
                                    ]
                                }
                            ]
                        }
                    ]
                }
            ]
        res = self._request("GET", f"/seat_maps?offer_id={offer_id}")
        return res if isinstance(res, list) else []

    async def create_order(self, params: Dict[str, Any]) -> Dict[str, Any]:
        if self.is_mock:
            return {
                "id": "ord_mock123456",
                "status": "held",
                "total_amount": "450.00",
                "total_currency": "USD",
                "expires_at": "2026-12-31T12:00:00Z",
                "booking_reference": "ABCXYZ",
                "passengers": [
                    {
                        "id": f"pax_{idx}",
                        "given_name": p["given_name"],
                        "family_name": p["family_name"],
                        "type": "adult"
                    } for idx, p in enumerate(params["passengers"])
                ],
                "slices": [
                    {
                        "origin": {"iata_code": "JFK"},
                        "destination": {"iata_code": "LAX"},
                        "duration": "PT6H30M",
                        "segments": [
                            {
                                "departing_at": "2026-07-15T08:00:00Z",
                                "arriving_at": "2026-07-15T14:30:00Z"
                            }
                        ]
                    }
                ]
            }
        
        order_payload = {
            "selected_offers": params["selectedOffers"],
            "passengers": params["passengers"],
            "type": "hold"
        }
        return self._request("POST", "/orders", order_payload)

    async def get_order(self, order_id: str) -> Dict[str, Any]:
        if self.is_mock:
            return {
                "id": order_id,
                "status": "held",
                "total_amount": "450.00",
                "total_currency": "USD",
                "booking_reference": "ABCXYZ",
                "created_at": "2026-06-25T12:00:00Z",
                "expires_at": "2026-12-31T12:00:00Z",
                "passengers": [
                    {
                        "id": "pax_0",
                        "given_name": "John",
                        "family_name": "Doe",
                        "type": "adult",
                        "email": "john@example.com",
                        "phone_number": "+1234567890"
                    }
                ],
                "slices": [
                    {
                        "id": "sli_mock",
                        "origin": {"iata_code": "JFK", "name": "John F. Kennedy Airport", "city_name": "New York"},
                        "destination": {"iata_code": "LAX", "name": "Los Angeles Airport", "city_name": "Los Angeles"},
                        "duration": "PT6H30M",
                        "segments": [
                            {
                                "id": "seg_mock",
                                "origin": {"iata_code": "JFK"},
                                "destination": {"iata_code": "LAX"},
                                "departing_at": "2026-07-15T08:00:00Z",
                                "arriving_at": "2026-07-15T14:30:00Z",
                                "marketing_carrier": {"name": "Mock Airlines"},
                                "marketing_carrier_flight_number": "MK123",
                                "aircraft": {"name": "Boeing 787"}
                            }
                        ]
                    }
                ]
            }
        return self._request("GET", f"/orders/{order_id}")

    async def cancel_order(self, order_id: str) -> Dict[str, Any]:
        if self.is_mock:
            return {
                "id": "ocr_mock123456",
                "refund_amount": "450.00",
                "refund_currency": "USD",
                "confirmed_at": "2026-06-25T12:30:00Z"
            }
        cancel_payload = {"order_id": order_id}
        return self._request("POST", "/order_cancellations", cancel_payload)

    async def get_airlines(self) -> List[Dict[str, Any]]:
        if self.is_mock:
            return [
                {"iata_code": "AA", "name": "American Airlines"},
                {"iata_code": "DL", "name": "Delta Air Lines"},
                {"iata_code": "UA", "name": "United Airlines"},
                {"iata_code": "BA", "name": "British Airways"}
            ]
        res = self._request("GET", "/airlines")
        return res if isinstance(res, list) else []


# 4. Controllers Exposing Tools
@injectable(deps=[DuffelService])
class FlightTools:
    def __init__(self, service: DuffelService):
        self.service = service

    @tool(
        name="search_flights",
        title="Search Flights",
        description="Search for flight offers based on origin, destination, dates, and preferences.",
        input_schema=SearchFlightsInput
    )
    @use_guards(OAuthGuard, create_scope_guard(["read"]))
    @widget("flight-search-results")
    async def search_flights(self, input: SearchFlightsInput, context: ExecutionContext) -> dict:
        context.logger.info(f"Searching flights from {input.origin} to {input.destination}")
        return await self.service.search_flights(input.model_dump())

    @tool(
        name="get_flight_details",
        title="Get Flight Details",
        description="Get detailed information about a specific flight offer including baggage allowance, conditions.",
        input_schema=FlightDetailsInput
    )
    @use_guards(OAuthGuard, create_scope_guard(["read"]))
    @widget("flight-details")
    async def get_flight_details(self, input: FlightDetailsInput, context: ExecutionContext) -> dict:
        context.logger.info(f"Fetching flight details for offer {input.offerId}")
        return await self.service.get_offer(input.offerId)

    @tool(
        name="search_airports",
        title="Search Airports",
        description="Search for airports by query string.",
        input_schema=AirportSearchInput
    )
    @use_guards(OAuthGuard, create_scope_guard(["read"]))
    @widget("airport-search")
    async def search_airports(self, input: AirportSearchInput, context: ExecutionContext) -> dict:
        context.logger.info(f"Searching airports for query: {input.query}")
        return {"airports": [{"iataCode": "JFK", "name": "John F. Kennedy Airport"}]}

    @tool(
        name="get_airlines",
        title="Get Airlines",
        description="Get list of common airlines.",
        input_schema=GetAirlinesInput
    )
    @use_guards(OAuthGuard, create_scope_guard(["read"]))
    async def get_airlines(self, input: GetAirlinesInput, context: ExecutionContext) -> dict:
        context.logger.info("Fetching common airlines")
        res = await self.service.get_airlines()
        return {"airlines": res}


@injectable(deps=[DuffelService])
class BookingTools:
    def __init__(self, service: DuffelService):
        self.service = service

    @tool(
        name="create_order",
        title="Create Order",
        description="Create a flight order with hold (no payment required). Collect passenger details first.",
        input_schema=CreateOrderInput
    )
    @use_guards(OAuthGuard, create_scope_guard(["write"]))
    @widget("order-summary")
    async def create_order(self, input: CreateOrderInput, context: ExecutionContext) -> dict:
        context.logger.info(f"Creating flight order for offer {input.offerId}")
        try:
            passengers_array = json.loads(input.passengers)
        except Exception:
            raise ValueError("Invalid passengers JSON format")
            
        passengers = []
        for p in passengers_array:
            passengers.append({
                "title": p.get("title", "mr"),
                "given_name": p.get("givenName"),
                "family_name": p.get("familyName"),
                "gender": p.get("gender", "M"),
                "born_on": p.get("bornOn"),
                "email": p.get("email"),
                "phone_number": p.get("phoneNumber")
            })
            
        order_params = {
            "selectedOffers": [input.offerId],
            "passengers": passengers
        }
        return await self.service.create_order(order_params)

    @tool(
        name="get_order_details",
        title="Get Order Details",
        description="Get detailed information about an order.",
        input_schema=OrderDetailsInput
    )
    @use_guards(OAuthGuard, create_scope_guard(["read"]))
    @widget("order-summary")
    async def get_order_details(self, input: OrderDetailsInput, context: ExecutionContext) -> dict:
        context.logger.info(f"Fetching details for order {input.orderId}")
        return await self.service.get_order(input.orderId)

    @tool(
        name="get_seat_map",
        title="Get Seat Map",
        description="Get available seats for a flight offer to allow seat selection.",
        input_schema=SeatMapInput
    )
    @use_guards(OAuthGuard, create_scope_guard(["read"]))
    @widget("seat-selection")
    async def get_seat_map(self, input: SeatMapInput, context: ExecutionContext) -> dict:
        context.logger.info(f"Fetching seat map for offer {input.offerId}")
        res = await self.service.get_seats_for_offer(input.offerId)
        return {"offerId": input.offerId, "cabins": res}

    @tool(
        name="cancel_order",
        title="Cancel Order",
        description="Cancel a flight order and request refund if applicable.",
        input_schema=CancelOrderInput
    )
    @use_guards(OAuthGuard, create_scope_guard(["write"]))
    @widget("order-cancellation")
    async def cancel_order(self, input: CancelOrderInput, context: ExecutionContext) -> dict:
        context.logger.info(f"Cancelling order {input.orderId}")
        return await self.service.cancel_order(input.orderId)


# 5. Flight Prompts
@injectable(deps=[DuffelService])
class FlightPrompts:
    def __init__(self, service: DuffelService):
        self.service = service

    @prompt(
        name="flight_search_assistant",
        description="An AI assistant specialized in helping users search for flights and make booking decisions."
    )
    async def flight_search_assistant(self, args: dict, context: ExecutionContext) -> str:
        return "You are a professional flight booking assistant. Help the user search for flights using search_flights, search_airports, and assist in booking holds."

    @prompt(
        name="flight_comparison",
        description="Compare multiple flight offers and provide recommendations."
    )
    async def flight_comparison(self, args: dict, context: ExecutionContext) -> str:
        return "Compare the provided flight offer details, check layover times, durations, and pricing, and give the user a summary of best options."


# 6. Flight Resources
@injectable(deps=[DuffelService])
class FlightResources:
    def __init__(self, service: DuffelService):
        self.service = service

    @resource(
        uri="flight://popular-routes",
        name="Popular Flight Routes",
        description="Information about popular routes and pricing",
        mime_type="application/json"
    )
    async def popular_routes(self, context: ExecutionContext) -> dict:
        return {
            "routes": [
                {"route": "JFK -> LAX", "price": "$200-400"},
                {"route": "LHR -> JFK", "price": "$400-800"}
            ]
        }

    @resource(
        uri="flight://booking-guide",
        name="Flight Booking Guide",
        description="Guide on searching and booking flights",
        mime_type="text/markdown"
    )
    async def booking_guide(self, context: ExecutionContext) -> str:
        return "# Flight Booking Guide\n\n1. Search flights.\n2. Collect passenger info.\n3. Create order hold."


# 7. Health Check
class SystemHealthCheck:
    def __init__(self):
        self.start_time = time.time()

    @health_check("system")
    def check_system(self) -> bool:
        uptime = time.time() - self.start_time
        return uptime >= 0


# 8. Modules
@module(
    name="flights",
    controllers=[FlightTools, BookingTools, FlightPrompts, FlightResources],
    providers=[DuffelService],
    exports=[DuffelService]
)
class FlightsModule:
    pass

@module(
    name="app",
    imports=[
        # Configure OAuth resource protection
        OAuthModule.for_root(
            resource_uri="https://mcplocal",
            authorization_servers=["https://dev-5dt0utuk315713tjm.us.auth0.com"],
            scopes_supported=["read", "write", "admin"],
            token_introspection_endpoint=os.environ.get("INTROSPECTION_ENDPOINT")
        ),
        FlightsModule
    ],
    controllers=[],
    providers=[SystemHealthCheck],
    exports=[]
)
class AppModule:
    pass


# 9. Application Entrypoint
@mcp_app(
    module=AppModule,
    server=ServerConfig(name="airline-ticketing-server", version="1.0.0")
)
class App:
    pass

async def main():
    sys.stderr.write("🔐 Starting Airline Ticketing MCP Server with OAuth 2.1...\n")
    sys.stderr.flush()
    app = await McpApplicationFactory.create(App)
    await app.start()

if __name__ == "__main__":
    asyncio.run(main())
