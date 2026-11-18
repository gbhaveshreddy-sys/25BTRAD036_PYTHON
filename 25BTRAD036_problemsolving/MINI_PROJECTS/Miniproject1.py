import secrets

def generate_otp():
otp = &#39;&#39;.join(str(secrets.randbelow(6)) for _ in range(4))
return otp

otp = generate_otp()
print(";Your OTP is:";, otp)
