from flask import Flask, render_template, request, redirect, url_for, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from google.oauth2.service_account import Credentials
import os
import gspread

app = Flask(__name__, static_url_path='/static')
app.config['SECRET_KEY'] = 'your-secret-key-here'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///forms.db'
app.config['STATIC_FOLDER'] = 'static'
db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# Google Sheets Integration
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
SERVICE_ACCOUNT_FILE = os.path.join(os.getcwd(), 'google_sheets_key.json')

# Load credentials and authorize gspread
credentials = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
gc = gspread.authorize(credentials)

# Path to your Google Sheet
SHEET_ID = "1pGCyXZbZ0LQLNBUYfyWnS1oZdwXAz-2FSH_Gk_cE1l0"
sheet = gc.open_by_key(SHEET_ID).sheet1

# Database Models
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True)
    password = db.Column(db.String(100))

class Form(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100))
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    questions = db.relationship('Question', backref='form', lazy=True)
    responses = db.relationship('Response', backref='form', lazy=True)

class Question(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.String(500), nullable=False)
    question_type = db.Column(db.String(50), nullable=False)  # text, paragraph, mcq, checkbox
    options = db.Column(db.String(500))
    form_id = db.Column(db.Integer, db.ForeignKey('form.id'), nullable=False)

class Response(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    form_id = db.Column(db.Integer, db.ForeignKey('form.id'))
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    answers = db.relationship('Answer', backref='response', lazy=True)
    user = db.relationship('User', backref='responses')

class Answer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.String(1000), nullable=False)
    question_id = db.Column(db.Integer, db.ForeignKey('question.id'), nullable=False)
    response_id = db.Column(db.Integer, db.ForeignKey('response.id'), nullable=False)
    question = db.relationship('Question', backref='answers', lazy='joined')

# Authentication Routes
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = generate_password_hash(request.form['password'])
        user = User(username=username, password=password)
        db.session.add(user)
        db.session.commit()
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form['username']).first()
        if user and check_password_hash(user.password, request.form['password']):
            login_user(user)
            return redirect(url_for('dashboard'))
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# Application Routes
@app.route('/')
@login_required
def dashboard():
    forms = Form.query.filter_by(user_id=current_user.id).all()
    return render_template('dashboard.html', forms=forms)

@app.route('/create', methods=['GET', 'POST'])
@login_required
def create_form():
    if request.method == 'POST':
        form = Form(title=request.form['title'], user_id=current_user.id)
        db.session.add(form)
        
        for key in request.form:
            if key.startswith('question'):
                q_num = key[8:]
                question_type = request.form[f'type{q_num}']
                options = request.form.get(f'options{q_num}', '')
                question = Question(
                    content=request.form[key],
                    question_type=question_type,
                    options=options,
                    form=form
                )
                db.session.add(question)
        
        db.session.commit()
        return redirect(url_for('dashboard'))
    return render_template('create.html')

@app.route('/form/<int:form_id>', methods=['GET', 'POST'])
@login_required
def view_form(form_id):
    form = Form.query.get_or_404(form_id)
    if request.method == 'POST':
        response = Response(form_id=form_id, user_id=current_user.id)
        db.session.add(response)
        
        # Create a list to store form answers, which will be sent to Google Sheets
        answers_list = [current_user.username]  # Optionally, add the username
        
        for question in form.questions:
            answer_content = ', '.join(request.form.getlist(f'q{question.id}'))
            answers_list.append(answer_content)
            
            # Save answers to the database
            answer = Answer(
                content=answer_content,
                question_id=question.id,
                response=response
            )
            db.session.add(answer)
        
        db.session.commit()

        # Send answers to Google Sheets
        sheet.append_row(answers_list)  # This will add the form answers to the next available row in the sheet
        
        return redirect(url_for('dashboard'))
    return render_template('form.html', form=form)

@app.route('/responses/<int:form_id>')
@login_required
def view_responses(form_id):
    form = Form.query.get_or_404(form_id)
    if form.user_id != current_user.id:
        return redirect(url_for('dashboard'))
    return render_template('responses.html', form=form)

# Public form access
@app.route('/form/<int:form_id>/public', methods=['GET', 'POST'])
def public_form(form_id):
    form = Form.query.get_or_404(form_id)
    
    if request.method == 'POST':
        # Handle both authenticated and anonymous users
        user_id = current_user.id if current_user.is_authenticated else None
        response = Response(form_id=form_id, user_id=user_id)
        db.session.add(response)
        
        # Create a list to store form answers, which will be sent to Google Sheets
        answers_list = ["Anonymous" if user_id is None else current_user.username]
        
        for question in form.questions:
            answer_content = ', '.join(request.form.getlist(f'q{question.id}'))
            answers_list.append(answer_content)
            
            # Save answers to the database
            answer = Answer(
                content=answer_content,
                question_id=question.id,
                response=response
            )
            db.session.add(answer)
        
        db.session.commit()

        # Send answers to Google Sheets
        sheet.append_row(answers_list)  # Add the answers to the next available row in the sheet
        
        return render_template('thankyou.html')
    
    return render_template('public_form.html', form=form)

# Thank you page
@app.route('/thankyou')
def thankyou():
    return render_template('thankyou.html')

# Add this route to serve static files
@app.route('/static/<path:filename>')
def serve_static(filename):
    return send_from_directory(app.config['STATIC_FOLDER'], filename)

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
