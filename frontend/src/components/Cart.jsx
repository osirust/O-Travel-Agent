import { BedDouble, Info, Plane, ShoppingBag, Trash2 } from 'lucide-react';
import { cartTotal } from '../lib/cart.js';
import { dateLabel, money, offerAmount } from '../lib/travel.js';
import { FLIGHT_CLASSES } from '../lib/flights.js';
import { Modal, SourceBadge } from './Ui.jsx';

export default function Cart({ items, trip, stale, onRemove, onClose, onBrowse }) {
  const total = cartTotal(items);
  const route = [trip?.origin, ...(trip?.stays || []).map(stay => stay.city)].filter(Boolean).join(' → ');
  return <Modal title="Корзина поездки" subtitle="Здесь собраны выбранные вами варианты." onClose={onClose} wide>
    <div className="cart-content">
      {stale && <div className="cart-notice"><Info size={18} /><p>Параметры поездки изменились. Выберите варианты заново, чтобы они соответствовали новому маршруту.</p></div>}
      {[['flights', Plane, 'Перелёт'], ['hotels', BedDouble, 'Отель']].map(([kind, Icon, title]) => {
        const offer = items[kind];
        return <div className="cart-item" key={kind}><span className="cart-icon"><Icon size={20} /></span><div className="cart-item-details"><span className="cart-item-label">{title}</span>{offer ? <><strong>{kind === 'hotels' ? offer.description || 'Выбранный отель' : route || 'Выбранный перелёт'}</strong><small>{kind === 'hotels' && trip?.stays?.[0] ? `${dateLabel(trip.stays[0].check_in)} — ${dateLabel(trip.stays[0].check_out)}` : FLIGHT_CLASSES[offer.details?.search_request?.cabin_class] || 'Детали маршрута в разделе перелётов'}</small><SourceBadge source={offer.source_type} small /></> : <><strong>Не выбран · необязательно</strong><button className="text-button" onClick={() => onBrowse(kind)}>Выбрать {kind === 'hotels' ? 'отель' : 'перелёт'} →</button></>}</div>{offer && <div className="cart-item-price"><strong>{money(offerAmount(offer), offer.currency)}</strong><small>{offer.price_kind?.includes('NIGHTLY') ? 'за ночь' : 'цена ориентировочная'}</small><button className="cart-remove" onClick={() => onRemove(kind)} aria-label={`Убрать ${kind === 'hotels' ? 'отель' : 'перелёт'} из корзины`}><Trash2 size={15} /> Убрать</button></div>}</div>;
      })}
      {items.flights?.details?.itinerary_complete === false && <div className="cart-notice"><Info size={18} /><p>Выбранный перелёт может охватывать только часть маршрута. Остальные участки и стоимость всей поездки нужно уточнить.</p></div>}
      <div className="cart-total"><div><span>Стоимость выбранных вариантов</span><small>Окончательные цены и наличие нужно подтвердить</small></div><strong>{total ? money(total.amount, total.currency) : 'Уточняется'}</strong></div>
      <div className="cart-notice"><Info size={18} /><p>Оплата пока недоступна: цены и наличие ещё не подтверждены. Выбранные варианты сохраняются в этом браузере.</p></div>
    </div><footer className="modal-footer"><button className="secondary-button" onClick={onClose}>Продолжить выбор</button><button className="primary-button" disabled><ShoppingBag size={17} /> Оплата пока недоступна</button></footer>
  </Modal>;
}
