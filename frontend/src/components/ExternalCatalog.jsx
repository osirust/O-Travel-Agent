import { useEffect, useState } from 'react';
import { ArrowRight, BedDouble, ChevronLeft, ChevronRight, Info, LoaderCircle, Plane, Search, Star } from 'lucide-react';
import { money, offerAmount } from '../lib/travel.js';
import { Modal, SourceBadge, TravelImage } from './Ui.jsx';

const sources = {
  hotels: [['serpapi', 'Google Hotels · SerpApi'], ['liteapi', 'LiteAPI · sandbox']],
  flights: [['serpapi', 'Google Flights · SerpApi'], ['duffel', 'Duffel · test'], ['travelpayouts', 'Aviasales · архив цен']],
};

function Reviews({ offer, load, onClose }) {
  const [state, setState] = useState({ loading: true, reviews: [], error: '' });
  useEffect(() => {
    let active = true;
    load(offer.offer_id).then(result => {
      if (active && result.offer_id === offer.offer_id) setState({ loading: false, reviews: result.reviews || [], error: '' });
    }).catch(error => { if (active) setState({ loading: false, reviews: [], error: error.message || 'Отзывы временно недоступны.' }); });
    return () => { active = false; };
  }, [offer.offer_id, load]);
  return <Modal title="Отзывы гостей" subtitle={offer.description} onClose={onClose} wide>
    <div className="reviews-content"><p className="reviews-source">Отзывы предоставлены Google Hotels через SerpApi. Мнение авторов, не проверка проживания нашей командой.</p>
      {state.loading ? <div className="review-empty"><LoaderCircle className="spin" size={20} /> Загружаем отзывы…</div> : state.error ? <p className="form-error">{state.error}</p> : state.reviews.length === 0 ? <p className="review-empty">Источник пока не вернул отзывы об этом отеле.</p> : state.reviews.map((review, i) => <article className="review-card" key={i}><div className="review-heading"><strong>{review.author || 'Гость'}</strong>{review.rating != null && <span><Star size={14} fill="currentColor" /> {review.rating}{review.best_rating ? ` / ${review.best_rating}` : ''}</span>}</div><small>{[review.date, review.source].filter(Boolean).join(' · ')}</small>{review.text && <p>{review.text}</p>}</article>)}
    </div>
  </Modal>;
}

function HotelCard({ offer, city, onReviews }) {
  const [photoIndex, setPhotoIndex] = useState(0);
  const info = offer.details?.provider_data || {};
  const photos = Array.isArray(info.photos) ? info.photos : [];
  const media = photos.length ? { url: photos[photoIndex % photos.length], alt: `Фото отеля ${offer.description}`, kind: 'HOTEL', source: 'SERPAPI', isActualPropertyPhoto: true } : null;
  const rating = typeof info.rating === 'number' ? info.rating : null;
  return <article className="catalog-hotel"><div className="catalog-photo"><TravelImage city={city} media={media} className="catalog-hotel-image" />{photos.length > 1 && <div className="photo-controls"><button type="button" onClick={() => setPhotoIndex((photoIndex + photos.length - 1) % photos.length)} aria-label={`Предыдущее фото ${offer.description}`}><ChevronLeft size={15} /></button><span>{photoIndex + 1} / {photos.length}</span><button type="button" onClick={() => setPhotoIndex((photoIndex + 1) % photos.length)} aria-label={`Следующее фото ${offer.description}`}><ChevronRight size={15} /></button></div>}</div>
    <div className="catalog-hotel-body"><div className="catalog-hotel-title"><div><p className="eyebrow">{city || 'ПРОЖИВАНИЕ'} · {offer.provider === 'liteapi' ? 'LITEAPI' : 'GOOGLE HOTELS'}</p><h3>{offer.description}</h3></div><SourceBadge source={offer.source_type} small /></div>
      <div className="catalog-rating">{rating !== null && <span className="rating-pill"><Star size={13} fill="currentColor" /> {rating.toFixed(1)} / 5</span>}{Number.isInteger(info.review_count) && <span>{info.review_count.toLocaleString('ru-RU')} отзывов</span>}{info.property_token && <button className="review-link" type="button" onClick={() => onReviews(offer)}>Читать отзывы <ArrowRight size={13} /></button>}{rating === null && !info.property_token && <span>Оценка пока недоступна</span>}</div>
      <div className="catalog-hotel-bottom"><div><strong>{money(offerAmount(offer), offer.currency)}</strong><p>{offer.price_kind?.includes('NIGHTLY') ? 'за ночь · итог уточняется' : 'за проживание · итог уточняется'}</p></div><span>Наличие не подтверждено</span></div>
    </div></article>;
}

export default function ExternalCatalog({ kind, data, onSearch, onEdit, loadReviews }) {
  const [provider, setProvider] = useState('serpapi');
  const [reviewOffer, setReviewOffer] = useState(null);
  const hotels = kind === 'hotels', offers = data.external[kind], Icon = hotels ? BedDouble : Plane;
  const trip = data.snapshot?.trip, currentSource = offers?.[0]?.provider;
  const available = data.providers?.[provider]?.configured;
  return <div className="external-offers"><div className="external-intro"><span className="external-icon"><Icon size={24} /></span><div><h2>{hotels ? 'Отели по вашему запросу' : 'Перелёты по вашему маршруту'}</h2><p>{hotels ? `${trip?.stays[0]?.city || 'Выберите город'} · первая остановка` : [trip?.origin, ...(trip?.stays || []).map(stay => stay.city)].filter(Boolean).join(' → ') || 'Заполните маршрут'}</p></div></div>
    <div className="reference-notice"><Info size={16} /><p>Предложения справочные или тестовые. Наличие, окончательная цена и бронирование не подтверждены.</p></div>
    <div className="catalog-toolbar"><label>Источник<select value={provider} onChange={event => setProvider(event.target.value)}>{sources[kind].map(([id, name]) => <option value={id} key={id}>{name}{data.providers?.[id]?.configured ? '' : ' · не настроен'}</option>)}</select></label><button className="primary-button" disabled={data.busy || data.sending || !available} onClick={() => onSearch(provider)}>{data.busy ? <LoaderCircle size={16} className="spin" /> : <Search size={16} />}{hotels ? 'Найти отели' : 'Найти перелёты'}</button><button className="text-button" onClick={onEdit}>Параметры <ArrowRight size={14} /></button></div>
    {!available && <p className="hint">Для этого источника нужен доступ, настроенный на сервере. Выберите подключённый API.</p>}
    {provider === 'travelpayouts' && <p className="hint">Архив цен только для первого перелёта, без подтверждения наличия и общей цены группы.</p>}
    {offers === null ? <div className="external-empty"><Icon size={35} strokeWidth={1.2} /><p>Нажмите «{hotels ? 'Найти отели' : 'Найти перелёты'}», чтобы получить предложения на сохранённые даты.</p></div> : offers.length === 0 ? <div className="external-empty"><Search size={32} /><p>Источник не вернул подходящих предложений. Попробуйте другие даты или направление.</p></div> : <><p className="catalog-result-heading">Найдено {offers.length} · {sources[kind].find(([id]) => id === currentSource)?.[1] || currentSource}</p><div className="external-list">{offers.map((offer, index) => {
      if (hotels) return <HotelCard key={offer.offer_id} offer={offer} city={trip?.stays?.[0]?.city} onReviews={setReviewOffer} />;
      const info = offer.details?.provider_data || {}, flights = Array.isArray(info.flights) ? info.flights : [];
      return <article className="external-card" key={offer.offer_id}><div className="section-title"><span className="external-card-icon"><Plane size={19} /></span><div><h3>Авиапредложение {index + 1}</h3><SourceBadge source={offer.source_type} small /></div><strong>{money(offerAmount(offer), offer.currency)}</strong></div><p className="external-price-scope">{offer.provider === 'travelpayouts' ? 'Архивная цена за первый перелёт; наличие и цена группы не подтверждены' : 'Цена источника для выбранного поиска; итоговую стоимость нужно уточнить'}</p>{flights.length > 0 && <div className="flight-segments">{flights.map((flight, i) => <div key={i}><span>{flight.airline || flight.airplane || 'Перелёт'}</span><p>{flight.departure_airport?.id || flight.departure_airport?.name || 'Отправление'} <ArrowRight size={13} />{flight.arrival_airport?.id || flight.arrival_airport?.name || 'Прибытие'}</p>{flight.departure_airport?.time && <small>{flight.departure_airport.time}</small>}</div>)}</div>}<p className="external-caption">Наличие и возможность бронирования не подтверждены</p></article>;
    })}</div></>}
    {reviewOffer && <Reviews key={reviewOffer.offer_id} offer={reviewOffer} load={loadReviews} onClose={() => setReviewOffer(null)} />}
  </div>;
}
