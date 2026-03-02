import enum
from datetime import datetime

from sqlalchemy import Integer, String, BigInteger, DateTime, Enum, Text
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class OrderStatus(str, enum.Enum):
    PENDING = "pending"           # Ожидает обработки
    GENERATING = "generating"     # Генерируется дизайн
    PREVIEW_SENT = "preview_sent" # Превью отправлено пользователю
    CONFIRMED = "confirmed"       # Заказ подтверждён
    IN_PRODUCTION = "in_production"  # В производстве
    SHIPPED = "shipped"           # Отправлен
    DELIVERED = "delivered"       # Доставлен
    CANCELLED = "cancelled"       # Отменён


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    telegram_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    telegram_first_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Файловые пути
    original_photo_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    generated_image_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    preview_image_path: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # Telegram file_id для быстрой отправки
    original_photo_file_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    generated_image_file_id: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # МойСклад
    moysklad_order_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    moysklad_order_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Статус и метаданные
    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus), default=OrderStatus.PENDING, nullable=False
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    def __repr__(self) -> str:
        return f"<Order id={self.id} user={self.telegram_user_id} status={self.status}>"
