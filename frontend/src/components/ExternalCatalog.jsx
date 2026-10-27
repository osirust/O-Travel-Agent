import { useEffect, useState } from 'react';
import { ArrowRight, BedDouble, ChevronLeft, ChevronRight, Info, LoaderCircle, Plane, Search, Star } from 'lucide-react';
import { componentRequest, money, offerAmount } from '../lib/travel.js';
import { EMPTY_HOTEL_FILTERS, filterHotels } from '../lib/hotelFilters.js';
import { Modal, SourceBadge, TravelImage } from './Ui.jsx';

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
    <div className="reviews-content"><p className="reviews-source">Отзывы путешественников, собранные Google. Они отражают личное мнение гостей.</p>
      {state.loading ? <div className="review-empty"><LoaderCircle className="spin" size={20} /> Загружаем отзывы…</div> : state.error ? <p className="form-error">{state.error}</p> : state.reviews.length === 0 ? <p className="review-empty">Источник пока не вернул отзывы об этом отеле.</p> : state.reviews.map((review, i) => <article className="review-card" key={i}><div className="review-heading"><strong>{review.author || 'Гость'}</strong>{review.rating != null && <span><Star size={14} fill="currentColor" /> {review.rating}{review.best_rating ? ` / ${review.best_rating}` : ''}</span>}</div><small>{[review.date, review.source].filter(Boolean).join(' · ')}</small>{review.text && <p>{review.text}</p>}</article>)}
    </div>
  </Modal>;
}

function HotelCard({ offer, city, onReviews, selected, onSelect }) {
  const [photoIndex, setPhotoIndex] = useState(0);
  const info = offer.details?.provider_data || {};
  const photos = Array.isArray(info.photos) ? info.photos : [];
  const media = photos.length ? { url: photos[photoIndex % photos.length], alt: `Фото отеля ${offer.description}`, kind: 'HOTEL', source: 'SERPAPI', isActualPropertyPhoto: true } : null;
  const rating = typeof info.rating === 'number' ? info.rating : null;
  return <article className="catalog-hotel"><div className="catalog-photo"><TravelImage city={city} media={media} className="catalog-hotel-image" />{photos.length > 1 && <div className="photo-controls"><button type="button" onClick={() => setPhotoIndex((photoIndex + photos.length - 1) % photos.length)} aria-label={`Предыдущее фото ${offer.description}`}><ChevronLeft size={15} /></button><span>{photoIndex + 1} / {photos.length}</span><button type="button" onClick={() => setPhotoIndex((photoIndex + 1) % photos.length)} aria-label={`Следующее фото ${offer.description}`}><ChevronRight size={15} /></button></div>}</div>
    <div className="catalog-hotel-body"><div className="catalog-hotel-title"><div><p className="eyebrow">{city || 'ПРОЖИВАНИЕ'}</p><h3>{offer.description}</h3></div><SourceBadge source={offer.source_type} small /></div>
      <div className="catalog-rating">{rating !== null && <span className="rating-pill"><Star size={13} fill="currentColor" /> {rating.toFixed(1)} / 5</span>}{Number.isInteger(info.review_count) && <span>{info.review_count.toLocaleString('ru-RU')} отзывов</span>}{info.property_token && <button className="review-link" type="button" onClick={() => onReviews(offer)}>Читать отзывы <ArrowRight size={13} /></button>}{rating === null && !info.property_token && <span>Оценка пока недоступна</span>}</div>
      <div className="catalog-hotel-bottom"><div><strong>{money(offerAmount(offer), offer.currency)}</strong><p>{offer.price_kind?.includes('NIGHTLY') ? 'за ночь · итог уточняется' : 'за проживание · итог уточняется'}</p></div><span>Наличие не подтверждено</span></div><button type="button" className={`cart-select-button ${selected ? 'selected' : ''}`} aria-pressed={selected} onClick={() => onSelect(selected ? null : offer)}>{selected ? '✓ В корзине' : 'Выбрать отель'}</button>
    </div></article>;
}

export default function ExternalCatalog({ kind, data, filters, setFilters, retry, loadReviews, selectedOffer, onSelectOffer }) {
  const [reviewOffer, setReviewOffer] = useState(null);
  const hotels = kind === 'hotels', offers = data.external[kind], Icon = hotels ? BedDouble : Plane;
  const trip = data.snapshot?.trip;
  const available = data.providers?.serpapi?.configured && data.providers?.serpapi?.free_tier_confirmed;
  const currency = trip?.budget?.currency || 'RUB';
  const currencyLabel = { RUB: '₽', USD: '$', EUR: '€' }[currency] || currency;
  const filtered = hotels ? filterHotels(offers || [], filters, currency) : null;
  let missing = '';
  if (trip) { try { componentRequest(trip, kind); } catch (error) { missing = error.message; } }
  const attemptKey = data.snapshot ? `${data.snapshot.session_id}:${data.snapshot.state_version}:${kind}` : null;
  const attempted = Boolean(attemptKey && data.autoAttempts?.[attemptKey]);
  const unknown = data.deliveries.some(entry => entry.kind === 'live/search' && entry.body.kind === kind && entry.body.expected_version === data.snapshot?.state_version && !entry.runId);
  const loading = data.busy || data.sending || data.restoring || (!attempted && !missing && available && data.online && !data.snapshot?.waiting_reason);
  const updateFilter = (name, value) => setFilters(current => ({ ...current, [name]: value }));
  return <div className="external-offers"><div className="external-intro"><span className="external-icon"><Icon size={24} /></span><div><h2>{hotels ? 'Отели по вашему запросу' : 'Перелёты по вашему маршруту'}</h2><p>{hotels ? `${trip?.stays[0]?.city || 'Выберите город'} · первая остановка` : [trip?.origin, ...(trip?.stays || []).map(stay => stay.city)].filter(Boolean).join(' → ') || 'Заполните маршрут'}</p></div></div>
    <div className="reference-notice"><Info size={16} /><p>Цены ориентировочные. Наличие мест и окончательную стоимость нужно уточнить.</p></div>
    {hotels && <div className="hotel-filters"><div className="hotel-filter-heading"><div><h3>Подберите отель</h3><p>Выберите подходящую оценку и стоимость</p></div>{Object.values(filters).some(Boolean) && <button type="button" className="text-button" onClick={() => setFilters({ ...EMPTY_HOTEL_FILTERS })}>Сбросить</button>}</div><div className="hotel-filter-grid"><fieldset><legend>Оценка · из 5</legend><div><label><span>От</span><input type="number" min="0" max="5" step="0.1" placeholder="0" aria-label="Оценка от" value={filters.ratingFrom} onChange={event => updateFilter('ratingFrom', event.target.value)} /></label><label><span>До</span><input type="number" min="0" max="5" step="0.1" placeholder="5" aria-label="Оценка до" value={filters.ratingTo} onChange={event => updateFilter('ratingTo', event.target.value)} /></label></div></fieldset><fieldset><legend>Цена за проживание · {currencyLabel}</legend><div><label><span>От</span><input type="number" min="0" step="0.01" placeholder="0" aria-label="Цена от" value={filters.priceFrom} onChange={event => updateFilter('priceFrom', event.target.value)} /></label><label><span>До</span><input type="number" min="0" step="0.01" placeholder="Любая" aria-label="Цена до" value={filters.priceTo} onChange={event => updateFilter('priceTo', event.target.value)} /></label></div></fieldset></div>{filtered.error && <p className="form-error" role="alert">{filtered.error}</p>}{filtered.active && !filtered.error && <p className="hint">Без оценки или цены за всё проживание отель не попадёт в соответствующий фильтр.</p>}</div>}
    {offers === null ? <div className="external-empty">{loading ? <LoaderCircle className="spin" size={32} /> : <Icon size={35} strokeWidth={1.2} />}<p>{!data.snapshot || data.restoring ? 'Загружаем вашу поездку…' : missing ? missing : data.snapshot.waiting_reason === 'user_pause' ? 'Поиск на паузе. Продолжите поездку, чтобы увидеть варианты.' : data.snapshot.waiting_reason ? 'Сначала уточните детали поездки.' : data.providers && !available ? 'Поиск временно недоступен. Попробуйте позже.' : unknown ? 'Проверим результат запроса, прежде чем повторять поиск.' : loading ? `Подбираем ${hotels ? 'отели' : 'перелёты'}…` : attempted ? 'Не удалось получить предложения. Попробуйте ещё раз.' : 'Готовим предложения…'}</p>{attempted && !unknown && !loading && !missing && !data.snapshot?.waiting_reason && available && <button className="secondary-button" type="button" onClick={retry}>Попробовать снова</button>}</div> : offers.length === 0 ? <div className="external-empty"><Search size={32} /><p>Не нашли подходящих предложений. Попробуйте другие даты или направление.</p></div> : hotels && (filtered.error || filtered.offers.length === 0) ? <div className="external-empty"><Search size={32} /><p>{filtered.error ? 'Проверьте параметры фильтра.' : 'По этим условиям отелей не нашлось. Попробуйте изменить оценку или цену.'}</p></div> : <><p className="catalog-result-heading">{hotels && filtered.active ? `Подходит ${filtered.offers.length} из ${offers.length}` : `Найдено предложений: ${offers.length}`}</p><div className="external-list">{(hotels ? filtered.offers : offers).map((offer, index) => {
      if (hotels) return <HotelCard key={offer.offer_id} offer={offer} city={trip?.stays?.[0]?.city} onReviews={setReviewOffer} selected={selectedOffer?.offer_id === offer.offer_id} onSelect={onSelectOffer} />;
      const info = offer.details?.provider_data || {}, flights = Array.isArray(info.flights) ? info.flights : [];
      return <article className="external-card" key={offer.offer_id}><div className="section-title"><span className="external-card-icon"><Plane size={19} /></span><div><h3>Вариант перелёта {index + 1}</h3><SourceBadge source={offer.source_type} small /></div><strong>{money(offerAmount(offer), offer.currency)}</strong></div><p className="external-price-scope">{offer.provider === 'travelpayouts' ? 'Цена за первый перелёт; наличие и стоимость для всей группы нужно уточнить' : 'Окончательную стоимость нужно уточнить'}</p>{flights.length > 0 && <div className="flight-segments">{flights.map((flight, i) => <div key={i}><span>{flight.airline || flight.airplane || 'Перелёт'}</span><p>{flight.departure_airport?.id || flight.departure_airport?.name || 'Отправление'} <ArrowRight size={13} />{flight.arrival_airport?.id || flight.arrival_airport?.name || 'Прибытие'}</p>{flight.departure_airport?.time && <small>{flight.departure_airport.time}</small>}</div>)}</div>}<p className="external-caption">Наличие мест нужно уточнить</p><button type="button" className={`cart-select-button ${selectedOffer?.offer_id === offer.offer_id ? 'selected' : ''}`} aria-pressed={selectedOffer?.offer_id === offer.offer_id} onClick={() => onSelectOffer(selectedOffer?.offer_id === offer.offer_id ? null : offer)}>{selectedOffer?.offer_id === offer.offer_id ? '✓ В корзине' : 'Выбрать перелёт'}</button></article>;
    })}</div></>}
    {reviewOffer && <Reviews key={reviewOffer.offer_id} offer={reviewOffer} load={loadReviews} onClose={() => setReviewOffer(null)} />}
  </div>;
}
