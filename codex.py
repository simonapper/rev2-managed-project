from openai import OpenAI
import sys

client = OpenAI()

prompt = " ".join(sys.argv[1:])

response = client.responses.create(
    model="gpt-4.1-mini",
    input=prompt
)

print(response.output_text)