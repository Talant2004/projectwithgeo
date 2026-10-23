import 'package:flutter/material.dart';

import '../settings.dart';

class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key, required this.settings});

  final AppSettings settings;

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  late final _url = TextEditingController(text: widget.settings.serverUrl);
  late final _user = TextEditingController(text: widget.settings.username);
  late final _password = TextEditingController(text: widget.settings.password);
  late bool _autoUrl = widget.settings.autoUrl;
  final _form = GlobalKey<FormState>();

  @override
  void dispose() {
    _url.dispose();
    _user.dispose();
    _password.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    if (!_form.currentState!.validate()) return;
    final s = AppSettings(
      serverUrl: _url.text.trim().replaceAll(RegExp(r'/+$'), ''),
      username: _user.text.trim(),
      password: _password.text,
      autoUrl: _autoUrl,
    );
    await s.save();
    if (mounted) Navigator.pop(context, s);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Настройки')),
      body: Form(
        key: _form,
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            SwitchListTile(
              contentPadding: EdgeInsets.zero,
              title: const Text('Адрес сервера автоматически'),
              subtitle: const Text('С того же сервера, что и «Повилика»'),
              value: _autoUrl,
              onChanged: (v) => setState(() => _autoUrl = v),
            ),
            TextFormField(
              controller: _url,
              enabled: !_autoUrl,
              keyboardType: TextInputType.url,
              decoration: InputDecoration(
                labelText: _autoUrl ? 'Текущий адрес GeoServer' : 'Адрес GeoServer',
                hintText: 'https://….trycloudflare.com/geoserver',
              ),
              validator: (v) {
                if (_autoUrl) return null;
                final uri = Uri.tryParse(v?.trim() ?? '');
                return uri != null && uri.hasScheme && uri.host.isNotEmpty
                    ? null
                    : 'Укажите полный адрес https://…';
              },
            ),
            const SizedBox(height: 8),
            Text('Маршруты: ${widget.settings.copyWith(serverUrl: _url.text.trim()).fieldnavUrl}',
                style: Theme.of(context).textTheme.bodySmall),
            const SizedBox(height: 16),
            TextFormField(
              controller: _user,
              decoration: const InputDecoration(labelText: 'Пользователь инспектора'),
            ),
            const SizedBox(height: 12),
            TextFormField(
              controller: _password,
              obscureText: true,
              decoration: const InputDecoration(labelText: 'Пароль'),
            ),
            const SizedBox(height: 24),
            FilledButton(onPressed: _save, child: const Text('Сохранить')),
          ],
        ),
      ),
    );
  }
}
