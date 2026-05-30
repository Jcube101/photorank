// Full-screen graceful state for errors and offline-at-rank. Never shows raw
// server detail — only the friendly message produced by api.js.

export default function MessageScreen({ eyebrow, title, body, actionLabel, onAction }) {
  return (
    <div className="pr-app">
      <div className="pr-scroll">
        <div className="message-screen">
          <div className="eyebrow ms-eyebrow">{eyebrow}</div>
          <div className="display ms-title">{title}</div>
          <div className="ms-body">{body}</div>
          <div className="ms-actions">
            <button onClick={onAction}>{actionLabel}</button>
          </div>
        </div>
      </div>
    </div>
  );
}
