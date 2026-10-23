import 'package:flutter/material.dart';

import 'screens/home_screen.dart';
import 'settings.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  ErrorWidget.builder = (details) => Material(
        color: Colors.white,
        child: Center(
          child: Padding(
            padding: const EdgeInsets.all(24),
            child: Text(
              'Ошибка отрисовки:\n${details.exception}',
              textAlign: TextAlign.center,
              style: const TextStyle(color: Colors.red, fontSize: 16),
            ),
          ),
        ),
      );

  // SharedPreferences на части Xiaomi падает (NoSuchMethodError) — UI всё равно показываем.
  AppSettings settings;
  try {
    settings = await AppSettings.load().timeout(const Duration(seconds: 3));
  } catch (_) {
    settings = AppSettings.defaults();
  }
  runApp(FieldRouteApp(settings: settings));
}

class FieldRouteApp extends StatelessWidget {
  const FieldRouteApp({super.key, required this.settings});

  final AppSettings settings;

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Полевой маршрут',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        colorSchemeSeed: const Color(0xFF1565C0),
        useMaterial3: true,
        brightness: Brightness.light,
        scaffoldBackgroundColor: Colors.white,
      ),
      home: HomeScreen(settings: settings),
    );
  }
}
