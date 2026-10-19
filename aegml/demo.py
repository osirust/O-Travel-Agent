"""Вымышленный статичный каталог. Цены и наличие не являются реальными."""

from .contracts import HotelOffer, TripRequest


class UnsupportedDestination(ValueError):
    pass


class DemoHotelProvider:
    # Цена за ночь для всей указанной группы в DEMO, без реального инвентаря.
    CATALOG = {
        "казань": ("Казань", (("kzn-1", "DEMO — Янтарный двор", 4800),
                            ("kzn-2", "DEMO — Тихая гавань", 6500))),
        "сочи": ("Сочи", (("sochi-1", "DEMO — Морской эскиз", 6200),
                          ("sochi-2", "DEMO — Южный сад", 8100))),
    }

    def search(self, request: TripRequest) -> list[HotelOffer]:
        key = request.destination.strip().casefold()
        if key not in self.CATALOG:
            raise UnsupportedDestination(
                "В DEMO-каталоге доступны только Казань и Сочи. "
                "Для какого из этих городов показать вымышленные примеры?"
            )
        destination, hotels = self.CATALOG[key]
        offers = []
        for identifier, name, nightly_rub in hotels:
            total = nightly_rub * request.nights
            if request.max_total_rub is not None and total > request.max_total_rub:
                continue
            offers.append(HotelOffer(
                id=f"demo-{identifier}",
                name=name,
                destination=destination,
                total_rub=total,
                nights=request.nights,
                source="fictional_static_catalog",
                is_demo=True,
                availability="demo_only_not_verified",
            ))
        return offers
