def validate_id_card(id_card: str) -> str | None:
    """校验 18 位身份证号，合法返回 None，不合法返回错误描述"""
    if len(id_card) != 18:
        return "身份证号必须为 18 位"
    if not id_card[:17].isdigit():
        return "身份证号前 17 位必须为数字"
    weights = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
    check_chars = "10X98765432"
    total = sum(int(id_card[i]) * weights[i] for i in range(17))
    expected = check_chars[total % 11]
    if id_card[17].upper() != expected:
        return "身份证号校验位不正确"
    return None
