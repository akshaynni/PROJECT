from flask import Flask, render_template, session, flash, request, send_file, redirect, url_for
import os
from subprocess import run
import google.generativeai as genai
from pdf2image import convert_from_path
import fitz  # PyMuPDF
from moviepy.editor import ImageClip, AudioFileClip, concatenate_videoclips
from google.cloud import texttospeech
from werkzeug.security import generate_password_hash, check_password_hash
from db_config import get_db_connection
# from pydub import AudioSegment
import random
import re
import time
import json
import glob

app = Flask(__name__)

# Set up the environment variable for Google Cloud credentials
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "credentials/projectrit-bb9d783f6699.json"

# Configure the Google Generative AI API with your API key
genai.configure(api_key="AIzaSyCjsqbMcPSRUrDjAyiP4A8UfKiI75FizG0")

model = genai.GenerativeModel('gemini-1.5-flash')

# Global variable to store LaTeX code temporarily
generated_latex = ""

app.secret_key = os.getenv('SECRET_KEY', 'secrets.token_hex(16)')
salutations = ["Moving on,", "Next up,", "Let's continue,"]

@app.route('/')
def index():
    return render_template('index.html', logged_in=False)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute('SELECT * FROM users WHERE email = %s', (email,))
        user = cursor.fetchone()
        conn.close()

        # flash(f'User from DB: {user}')
        if user:
            if check_password_hash(user['password'], password):
                session['user_id'] = user['id']
                flash('Login successful!', 'success')
                return redirect(url_for('home'))
            else:
                flash('Invalid Password', 'danger')
        else:
            flash('Invalid credentials, please try again.', 'danger')
    return render_template('login.html', logged_in=False, high_priority_tasks=None)


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form['email']
        phone = request.form['phone']
        profession = request.form['profession']
        password = request.form['password']
        confirm_password = request.form['confirm_password']

        if password != confirm_password:
            flash('Passwords do not match.', 'danger')
            return redirect(url_for('register'))

        hashed_password = generate_password_hash(password, method='pbkdf2:sha256', salt_length=16)

        conn = get_db_connection()
        cursor = conn.cursor()

        # Correct the query parameter to be a tuple by adding a comma after (email)
        cursor.execute('SELECT * FROM users WHERE email = %s', (email,))
        user = cursor.fetchone()

        conn.commit()

        # Correct the query parameter to be a tuple by adding a comma after (phone)
        cursor.execute('SELECT * FROM users WHERE phone = %s', (phone,))
        ph_number = cursor.fetchone()

        conn.commit()

        # Insert new user if email and phone do not exist
        if not user and not ph_number:
            cursor.execute('INSERT INTO users (email, phone, profession, password) VALUES (%s, %s, %s, %s)',
                           (email, phone, profession, hashed_password))
        else:
            flash('Account already exists')
            return render_template('register.html', logged_in=False, high_priority_tasks=None)

        conn.commit()
        conn.close()

        flash('Registration successful! You can now log in.', 'success')
        return redirect(url_for('login'))

    return render_template('register.html', logged_in=False, high_priority_tasks=None)
@app.route('/home')
def home():
    return render_template('index.html', logged_in=True)

@app.route('/generate', methods=['POST'])
def generate():
    global generated_latex
    subject = request.form['subject']
    # Convert the input to an integer before adding 1
    slides = int(request.form['slides']) + 1

    format_latex = r"""
    \\documentclass{beamer}
    \\usetheme{Madrid}
    
    """
    folder='generated_files'
    clear_generated_files(folder)
    details =request.form['details']

    # Generate LaTeX content using the AI model
    response = model.generate_content(f"Generate code with {slides} slides of the {format_latex} about {subject} without comments and exclude ```tex``` ,first page with student details  {details} and topic name  and end with a thank you slide")

    # Get the actual LaTeX content from the response
    generated_latex = response.text

    # Complete the LaTeX document by closing it
    generated_latex += r"\end{document}"

    # Render the LaTeX response on a webpage to check the output
    return render_template('generated.html', latex_code=generated_latex)


@app.route('/generate_pdf', methods=['POST'])
def generate_pdf():
    global generated_latex

    # Write LaTeX to a .tex file
    tex_file_path = 'generated_files/presentation.tex'
    with open(tex_file_path, 'w') as f:
        f.write(generated_latex)

    # Compile LaTeX to PDF
    pdf_file_path = compile_latex_to_pdf(tex_file_path)

    # Redirect to download the generated PDF file
    return redirect(url_for('download_pdf'))


@app.route('/generate_video', methods=['POST'])
def generate_video():
    if not os.path.exists('generated_files/presentation.pdf'):
        global generated_latex

        # Write LaTeX to a .tex file
        tex_file_path = 'generated_files/presentation.tex'
        with open(tex_file_path, 'w') as f:
            f.write(generated_latex)
        pdf_file_path = compile_latex_to_pdf(tex_file_path)

    pdf_file_path = 'generated_files/presentation.pdf'

    image_folder = 'static/images'
    audio_folder = 'static/audio'

    # Clear existing images and audio files
    clear_generated_files(image_folder)
    clear_generated_files(audio_folder)

    # Convert PDF to images
    images = convert_from_path(pdf_file_path)
    for i, image in enumerate(images):
        image_path = f'static/images/slide_{i+1}.png'
        image.save(image_path)

    # Extract text from PDF
    text_list = pdf_to_text_list(pdf_file_path)

    # Generate narration and audio for each slide
    cleaned_words = generate_narrations(text_list)
    for i, text in enumerate(cleaned_words):
        audio_filename = f'static/audio/slide_{i + 1}.mp3'
        synthesize_text_to_speech(text, audio_filename)

    # Create video clips from images and audio
    video_file_path = create_video_from_images_and_audio(len(images))
    return redirect(url_for('download_video'))


@app.route('/download_pdf')
def download_pdf():
    pdf_file_path = 'generated_files/presentation.pdf'
    if os.path.exists(pdf_file_path):
        return send_file(pdf_file_path, as_attachment=True)
    else:
        return "PDF generation failed. Please try again."


@app.route('/download_video')
def download_video():
    video_file_path = 'generated_files/presentation.mp4'
    if os.path.exists(video_file_path):
        return send_file(video_file_path, as_attachment=True)
    else:
        return "Video generation failed. Please try again."

def clear_generated_files(folder):
    """Delete all files in the 'generated_files' directory."""
    files = glob.glob(f'{folder}/*')

    for f in files:
        try:
            os.remove(f)
        except Exception as e:
            print(f"Error deleting file {f}: {e}")
def compile_latex_to_pdf(tex_file_path):
    """Compile LaTeX to PDF."""
    try:
        run(['pdflatex', '-output-directory=generated_files', tex_file_path], check=True)
        return 'generated_files/presentation.pdf'
    except Exception as e:
        print(f"Error during LaTeX compilation: {e}")
        return None


def pdf_to_text_list(pdf_path):
    """Extract text from each page of the PDF."""
    document = fitz.open(pdf_path)
    text_list = []

    for page_num in range(len(document)):
        page = document.load_page(page_num)
        text = page.get_text()
        text_list.append(text)

    document.close()
    return text_list

def generate_narrations(text_list):
    """Generate narration for each slide using AI and clean the text."""
    responses = []

    # Generate the first slide's response
    try:
        first_slide_response = model.generate_content(
            text_list[0] + " just introduce about the subject "
        )
        responses.append(first_slide_response.text)
    except Exception as e:
        print(f"Error generating content for the first slide: {e}")
        responses.append("Introduction slide content could not be generated.")

    # Generate responses for the remaining slides
    for txt in text_list[1:-1]:
        try:
            salutation = random.choice(salutations)
            if txt.strip():
                response = model.generate_content(
                    txt + " explain in short paragraph  starts with a " + salutation
                )
                responses.append(response.text)
                time.sleep(0.2)
        except Exception as e:
            print(f"Error generating content for text: {txt}. Error: {e}")
            responses.append("Content generation error.")

    # Generate response for the last slide
    try:
        last_slide_text = text_list[-1].strip().lower()
        if last_slide_text.startswith("thank you") or last_slide_text.startswith("questions"):
            last_slide_response = model.generate_content(text_list[-1] + " print this word as it is ")
        else:
            last_slide_response = model.generate_content(
                "Finally, " + text_list[-1] + " explain in short paragraph "
            )
        responses.append(last_slide_response.text)
    except Exception as e:
        print(f"Error generating content for the last slide: {e}")
        responses.append("Closing slide content could not be generated.")

    # Clean the responses
    cleaned_words = [clean_word(word) for word in responses]
    return cleaned_words

def clean_word(word):
    """Clean unwanted symbols from the text except for '.' and ','."""
    return re.sub(r'[^\w\s\.,:]', '', word)

def synthesize_text_to_speech(text, filename):
    """Convert text to speech and save as MP3."""
    client = texttospeech.TextToSpeechClient()
    synthesis_input = texttospeech.SynthesisInput(text=text)
    voice = texttospeech.VoiceSelectionParams(
        language_code="en-US",
        ssml_gender=texttospeech.SsmlVoiceGender.NEUTRAL
    )
    audio_config = texttospeech.AudioConfig(audio_encoding=texttospeech.AudioEncoding.MP3)

    response = client.synthesize_speech(
        input=synthesis_input, voice=voice, audio_config=audio_config
    )
    with open(filename, "wb") as out:
        out.write(response.audio_content)


def create_video_from_images_and_audio(num_images):
    """Create video from images and corresponding audio."""
    clips = []
    for i in range(1, num_images + 1):
        image_path = f'static/images/slide_{i}.png'
        audio_path = f'static/audio/slide_{i}.mp3'

        img_clip = ImageClip(image_path)
        audio_clip = AudioFileClip(audio_path)

        img_clip = img_clip.set_duration(audio_clip.duration)
        img_clip = img_clip.set_audio(audio_clip)

        clips.append(img_clip)

    final_video = concatenate_videoclips(clips, method="compose")
    video_file_path = "generated_files/presentation.mp4"
    final_video.write_videofile(video_file_path, fps=24)
    return video_file_path

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    flash('Logged out successfully.', 'success')
    return redirect(url_for('index'))

if __name__ == '__main__':
    # Ensure necessary directories exist
    os.makedirs('generated_files', exist_ok=True)
    os.makedirs('static/images', exist_ok=True)
    os.makedirs('static/audio', exist_ok=True)

    app.run(debug=True)