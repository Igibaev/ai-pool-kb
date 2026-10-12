"""Рассылка SMS должникам. Запускается 5 числа вручную специалистом абонентского отдела.
Вход: выгрузка из биллинга dolzhniki.csv (делает администратор биллинга 1 числа).
"""
import csv, datetime as dt, requests

GATEWAY = "https://sms.example-operator.kz/send"   # шлюз оператора, ключ в файле key.txt
MIN_MONTHS = 2          # долг от 2 месяцев
MIN_DEBT = 10000        # меньше 10 000 тг не беспокоим
REPEAT_DAYS = 30        # не чаще раза в 30 дней
TEXT = "Сарыарка Су: задолженность за воду {debt} тг. Оплатите до {deadline}, иначе подача воды будет ограничена. Справки: 8 7212 00-00-00"


def load(path):
    with open(path, encoding="cp1251") as f:
        return list(csv.DictReader(f, delimiter=";"))


def should_send(row, today):
    if int(row["месяцев долга"]) < MIN_MONTHS:
        return False
    if int(row["долг"].replace(" ", "")) < MIN_DEBT:
        return False
    last = row.get("дата последнего уведомления")
    if last:
        last = dt.datetime.strptime(last, "%d.%m.%Y").date()
        if (today - last).days < REPEAT_DAYS:
            return False
    if not row.get("телефон"):
        return False
    return True


def main():
    today = dt.date.today()
    deadline = (today + dt.timedelta(days=10)).strftime("%d.%m.%Y")
    rows = load("dolzhniki.csv")
    sent = 0
    for r in rows:
        if not should_send(r, today):
            continue
        requests.post(GATEWAY, data={"to": r["телефон"], "text": TEXT.format(debt=r["долг"], deadline=deadline)})
        sent += 1
    print("отправлено", sent)
    # TODO: записывать дату отправки обратно в Excel — пока вручную


if __name__ == "__main__":
    main()
