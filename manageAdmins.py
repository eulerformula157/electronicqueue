import sys
import argparse
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, Column, Integer, String
from sqlalchemy.orm import sessionmaker, declarative_base
import bcrypt

# Загружаем настройки из main.env
load_dotenv("main.env")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:password@localhost:5432/postgres")

Base = declarative_base()

class Admin(Base):
    __tablename__ = 'admins'
    id = Column(Integer, primary_key=True)
    login = Column(String, unique=True, nullable=False)
    password = Column(String, nullable=False)
    status = Column(String, nullable=False)

def get_password_hash(password: str) -> str:
    pwd_bytes = password.encode("utf-8")
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(pwd_bytes, salt).decode("utf-8")

def main():
    # Использование RawDescriptionHelpFormatter позволяет выводить примеры красиво
    parser = argparse.ArgumentParser(
        description="Инструмент управления администраторами и терминалами СУО.",
        formatter_class=argparse.RawDescriptionHelpFormatter
        )
    
    subparsers = parser.add_subparsers(dest="command", title="Доступные команды", help="Используйте [команда] -h для деталей")

    # Команда ADD
    parser_add = subparsers.add_parser('add', help='Регистрация нового пользователя')
    parser_add.add_argument('login', type=str, help='Уникальное имя пользователя для входа')
    parser_add.add_argument('password', type=str, help='Пароль (будет захеширован перед сохранением)')
    parser_add.add_argument('status', choices=['admin', 'terminal'], 
                            help='Роль: admin (доступ к панели) или terminal (информационное табло)')

    # Команда DELETE
    parser_delete = subparsers.add_parser('delete', help='Полное удаление пользователя')
    parser_delete.add_argument('login', type=str, help='Логин пользователя, которого нужно удалить')

    # Команда SHOW
    parser_show = subparsers.add_parser('show', help='Вывод списка всех зарегистрированных записей')

    # Команда CHANGE
    parser_change = subparsers.add_parser('change', help='Редактирование существующих прав или пароля')
    parser_change.add_argument('login', type=str, help='Логин пользователя для модификации')
    parser_change.add_argument('--pass', dest='new_password', type=str, 
                               help='Укажите этот флаг, если нужно сменить пароль')
    parser_change.add_argument('--status', dest='new_status', choices=['admin', 'terminal'], 
                               help='Укажите этот флаг, если нужно изменить уровень доступа')

    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(1)

    args = parser.parse_args()

    engine = create_engine(DATABASE_URL)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    try:
        if args.command == 'add':
            hashed_pwd = get_password_hash(args.password)
            new_admin = Admin(login=args.login, password=hashed_pwd, status=args.status)
            db.add(new_admin)
            db.commit()
            print(f"Пользователь '{args.login}' успешно создан.")

        elif args.command == 'delete':
            admin = db.query(Admin).filter(Admin.login == args.login).first()
            if admin:
                db.delete(admin)
                db.commit()
                print(f"Пользователь '{args.login}' удален из базы.")
            else:
                print(f"Ошибка: Пользователь '{args.login}' не найден.")

        elif args.command == 'show':
            admins = db.query(Admin).all()
            if not admins:
                print("Список пуст.")
            else:
                print(f"\n{'ЛОГИН':<20} | {'СТАТУС':<15}")
                print("-" * 38)
                for a in admins:
                    print(f"{a.login:<20} | {a.status:<15}")
                print("")

        elif args.command == 'change':
            admin = db.query(Admin).filter(Admin.login == args.login).first()
            if not admin:
                print(f"Ошибка: Пользователь '{args.login}' не существует.")
                return

            if not args.new_password and not args.new_status:
                print("Ничего не указано для изменения. Используйте --pass или --status.")
                return

            if args.new_password:
                admin.password = get_password_hash(args.new_password)
                print(f"Пароль для '{args.login}' обновлен.")
            
            if args.new_status:
                admin.status = args.new_status
                print(f"Статус '{args.login}' изменен на {args.new_status}.")

            db.commit()

    except Exception as e:
        print(f"Критическая ошибка: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    main()