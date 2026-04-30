import subprocess

text = "Талон 34. Подойдите к окну Д1."

with open("test.wav", "wb") as f:
    subprocess.run(
        [
            "piper",
            "--model", "ru_RU-irina-medium.onnx",
            "--length-scale", "1.25",
            "--noise-scale", "0.65",
            "--noise-w-scale", "0.75",
        ],
        input=text,
        text=True,
        stdout=f,
        check=True
    )