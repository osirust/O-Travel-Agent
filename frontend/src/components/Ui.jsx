import { useEffect, useRef, useState } from 'react';
import { ArrowUpRight, ImageOff, X } from 'lucide-react';
import { destinationImage, safeHotelMedia, SOURCE_LABELS } from '../lib/travel.js';

export function Logo({ onClick, compact = false }) {
  return <button className="brand" onClick={onClick} aria-label="O!Travel — на главную"><span className="brand-mark" aria-hidden="true">O<span>!</span></span><span className="brand-name">Travel</span>{!compact && <span className="brand-subtitle">AI Travel Studio</span>}</button>;
}

export function SourceBadge({ source, small = false }) {
  return <span className={`source-badge source-${source?.toLowerCase() || 'unknown'} ${small ? 'small' : ''}`} title={source === 'DEMO' ? 'Вымышленные цена и наличие' : source === 'SANDBOX' ? 'Тестовая среда поставщика, реальная цена не подтверждена' : source === 'LIVE_INDICATIVE' ? 'Наличие и возможность бронирования не подтверждены' : undefined}>{SOURCE_LABELS[source] || 'Источник уточняется'}</span>;
}

export function TravelImage({ city, media, className = '', caption = true, eager = false }) {
  const actual = safeHotelMedia(media);
  const [failedSources, setFailedSources] = useState([]);
  const src = [actual?.url, destinationImage(city)].find(url => url && !failedSources.includes(url));
  const valid = Boolean(src);
  const showingActual = valid && src === actual?.url;
  return <div className={`travel-image ${className}`}>
    {valid ? <img src={src} alt={showingActual ? actual.alt || 'Фото отеля' : `${city || 'Путешествие'} — иллюстрация направления`} loading={eager ? 'eager' : 'lazy'} onError={() => setFailedSources(sources => [...sources, src])} /> : <div className="image-placeholder"><ImageOff size={30} strokeWidth={1.3} /><span>{city || 'Ваше путешествие'}</span></div>}
    {caption && <span className="image-caption">{showingActual ? 'Фото отеля · LiteAPI' : valid ? 'Иллюстрация направления' : 'Фото пока недоступно'}</span>}
  </div>;
}

export function Modal({ title, subtitle, onClose, children, wide = false }) {
  const ref = useRef(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const previous = document.activeElement;
    const dialog = ref.current;
    const getFocusable = () => [...dialog.querySelectorAll('button, input, select, textarea, a[href], [tabindex="0"]')].filter(el => !el.disabled && el.getClientRects().length);
    getFocusable()[0]?.focus();
    const onKey = event => {
      if (event.key === 'Escape') closeRef.current();
      if (event.key !== 'Tab') return;
      const elements = getFocusable();
      const first = elements[0], last = elements.at(-1);
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    const overflow = document.body.style.overflow; document.body.style.overflow = 'hidden';
    dialog.addEventListener('keydown', onKey);
    return () => { document.body.style.overflow = overflow; dialog.removeEventListener('keydown', onKey); previous?.focus(); };
  }, []);
  return <div className="modal-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}><section className={`modal ${wide ? 'modal-wide' : ''}`} role="dialog" aria-modal="true" aria-label={title} ref={ref}><header className="modal-header"><div><p className="eyebrow">O!TRAVEL STUDIO</p><h2>{title}</h2>{subtitle && <p className="muted">{subtitle}</p>}</div><button className="icon-button" onClick={onClose} aria-label="Закрыть"><X size={21} /></button></header>{children}</section></div>;
}

export function DestinationCard({ city, image, onClick }) {
  return <button className="destination-card" onClick={onClick}><img src={image} alt={`${city} — иллюстрация направления`} loading="lazy" /><span>{city}</span><i><ArrowUpRight size={22} /></i></button>;
}
